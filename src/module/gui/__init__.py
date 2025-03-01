from ..version import __version__
from ..config import AppConfig, AppTheme, load_config
from ..core.material import GameTarget, MaterialMode
from ..preset import Preset

from .style import STYLESHEET_TILE_REQUIRED, STYLESHEET, STYLESHEET_MIN
from .backend import CoreBackend, ImageRole

from typing import Any
from sys import argv, platform
from traceback import format_exc
import subprocess

from pathlib import Path
from PySide6.QtCore import Qt, Signal, Slot, QSize, QMimeData, QKeyCombination, QFileSystemWatcher, QTimer
from PySide6.QtGui import QDragEnterEvent, QMouseEvent, QImage, QPixmap, QColor, QDrag
from PySide6.QtWidgets import (
	QWidget, QMainWindow, QFrame, QApplication, QMessageBox, QMenuBar,
	QBoxLayout, QHBoxLayout, QVBoxLayout, QSizePolicy,
	QLabel, QLineEdit, QToolButton, QFileDialog,
	QGroupBox, QProgressBar, QPushButton, QComboBox
)

from urllib.parse import unquote_plus, urlparse
def uri_to_path(uri: str) -> str:
	return unquote_plus(urlparse(uri).path)

class QDataComboBox( QComboBox ):
	def setCurrentData(self, data: Any):
		index = -1
		for i in range(self.count()):
			if data == self.itemData(i):
				index = i
				break
		self.setCurrentIndex(index)

class RClickToolButton( QToolButton ):
	rightClicked = Signal( name='RightClicked' )
	def mouseReleaseEvent(self, e: QMouseEvent) -> None:
		if e.button() == Qt.MouseButton.RightButton: self.rightClicked.emit()
		else: self.clicked.emit()


class PickableImage( QFrame ):
	picked = Signal( str, Path, object, name='Picked', arguments=['Kind', 'Path', 'ReturnBack'] )
	''' Fires when an image has been picked from the filesystem. (Path|None) '''

	name: str
	kind: str
	required: bool
	path: Path|None = None
	
	path_box: QLineEdit
	iconButton: QToolButton
	icon: QPixmap

	def __init__(self, name: str, kind: str, required: bool, parent: QWidget | None = None, f: Qt.WindowType = Qt.WindowType.Widget) -> None:
		super().__init__(parent, f)
		self.name = name
		self.kind = kind
		self.required = required
		self.setAcceptDrops(True)

		layout = QHBoxLayout()
		layout.setContentsMargins(0, 0, 0, 0)
		layout.setSpacing(4)
		
		self.setLayout(layout)
		self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
		self.setFixedHeight(48)

		self.icon = QPixmap()
		self.iconButton = RClickToolButton()
		self.iconButton.setFixedSize(48, 48)
		self.iconButton.setIcon(self.icon)
		self.iconButton.setIconSize(QSize(48, 48))
		self.iconButton.clicked.connect(self.on_icon_click)
		self.iconButton.rightClicked.connect(self.on_icon_rclick)
		self.update_required()

		layout.addWidget(self.iconButton)
		vlayout = QVBoxLayout()
		layout.addLayout(vlayout)

		hlayout = QHBoxLayout()
		hlayout.setAlignment(Qt.AlignmentFlag.AlignLeft)
		hlayout.setSpacing(10)
		hlayout.setContentsMargins(0, 0, 0, 0)
		vlayout.addLayout(hlayout)

		hlayout.addWidget(QLabel(text=name))
		if self.required:
			hint_text = QLabel(text='(Required)')
			hint_text.setObjectName('hint')
			hlayout.addWidget(hint_text)

		self.path_box = QLineEdit()
		self.path_box.setEnabled(False)
		vlayout.addWidget(self.path_box)

	def update_required(self):
		if self.required:
			self.iconButton.setStyleSheet('' if self.path else STYLESHEET_TILE_REQUIRED)
			self.iconButton.update()

	def mousePressEvent(self, event: QMouseEvent) -> None:
		if self.path == None or event.button() != Qt.MouseButton.LeftButton:
			return super().mousePressEvent(event)
	
		drag = QDrag(self)
		mimeData = QMimeData()
		mimeData.setText(self.path.as_uri())
		drag.setMimeData(mimeData)
		drag.setHotSpot(event.position().toPoint())
		drag.exec()

	def dragEnterEvent(self, event: QDragEnterEvent) -> None:
		if event.mimeData().hasText():
			event.accept()
		else:
			event.ignore()

	def dropEvent(self, event):
		fileUrl = event.mimeData().text()
		rawFilePath = uri_to_path(fileUrl)
		if platform == 'win32': rawFilePath = rawFilePath[1:]
		filePath = Path(rawFilePath)
		if not filePath.is_file(): return
		event.accept()

		self.path = filePath
		self.reload()
	
	def set_icon(self, img: QImage|None):
		if img:
			self.icon = self.icon.fromImage(img)
			self.iconButton.setIcon(self.icon)
		else:
			self.icon.fill(QColor(0, 0, 0, 0))
			self.iconButton.setIcon(self.icon)
		print('Icon updated!')

	def on_icon_click(self):
		fileUrls = QFileDialog.getOpenFileNames(self, caption=f'Selecting {self.kind} image', filter='Images (*.png *.jpg *.jpeg *.bmp *.tga *.tiff *.hdr)')[0]
		if len(fileUrls) == 0: return
		
		url = Path(fileUrls[0])
		self.path = url
		self.reload()

	def on_icon_rclick(self):
		self.path = None
		self.reload()

	def reload(self):
		self.path_box.setText(self.path.name if self.path else '')
		self.picked.emit(self.kind, self.path, self.set_icon)
		self.update_required()

	@Slot()
	def from_preset(self, preset: Preset):
		self.path = preset.get_path(self.kind)
		self.reload()

class MainWindow( QMainWindow ):
	update_from_preset = Signal( Preset, name='UpdateFromPreset' )

	target: str|None = None
	exporting: bool = False
	watching: bool = False

	watcherCooldown: QTimer
	watcher: QFileSystemWatcher
	config: AppConfig
	backend: CoreBackend
	progressBar: QProgressBar

	def __init__(self, config: AppConfig, parent=None) -> None:
		#region init
		super().__init__(parent)

		self.setWindowTitle( 'PBR-2-Source v'+__version__ )
		self.setMinimumSize( 300, 450 )
		self.resize(600, 450)

		self.watcherCooldown = QTimer()
		self.watcherCooldown.setSingleShot(True)
		self.watcherCooldown.timeout.connect(self.export)
		# self.watcherCooldown.timeout.connect(lambda : print('YIPPEE!!'))

		self.watcher = QFileSystemWatcher(self)
		self.watcher.fileChanged.connect(self.on_file_changed)

		self.config = config
		self.backend = CoreBackend()

		#endregion
		''' ========================== MENU ========================== '''
		#region menu

		menuBar = QMenuBar(self)
		# menuBar.setNativeMenuBar(False)

		self.setMenuBar(menuBar)
		fileMenu = menuBar.addMenu('File')
		loadAction = fileMenu.addAction('Load Preset')
		loadAction.setShortcut(QKeyCombination(Qt.KeyboardModifier.ControlModifier, Qt.Key.Key_O))
		loadAction.triggered.connect(self.load_preset)

		saveAction = fileMenu.addAction('Save Preset')
		saveAction.setShortcut(QKeyCombination(Qt.KeyboardModifier.ControlModifier, Qt.Key.Key_S))
		saveAction.triggered.connect(self.save_preset)
		
		fileMenu.addSeparator()

		self.watchAction = fileMenu.addAction('Watch')
		self.watchAction.triggered.connect(self.watch)

		exportAction = fileMenu.addAction('Export')
		exportAction.triggered.connect(self.export)
		exportAction.setShortcut(QKeyCombination(Qt.KeyboardModifier.ControlModifier, Qt.Key.Key_E))
		
		exportAsAction = fileMenu.addAction('Export As...')
		exportAsAction.triggered.connect(self.export_as)
		exportAsAction.setShortcut(QKeyCombination(Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier, Qt.Key.Key_E))
		
		#endregion
		''' ========================== LAYOUT ========================== '''
		#region layout

		rootWidget = QWidget(self)
		root = QVBoxLayout(rootWidget)
		self.setCentralWidget(rootWidget)

		inner = QHBoxLayout()
		root.addLayout(inner)

		left = QGroupBox(title='Input')
		leftLayout = QVBoxLayout(left)
		inner.addWidget(left)

		right = QGroupBox(title='Output')
		rightLayout = QVBoxLayout(right)
		rightLayout.setAlignment(Qt.AlignmentFlag.AlignTop)
		inner.addWidget(right)

		footer = QHBoxLayout()
		root.addLayout(footer)

		# endregion
		''' ========================== LEFT ========================== '''
		#region left

		def registerWidgets(parent: QBoxLayout, entries: list[PickableImage]):
			for widget in entries:
				widget.picked.connect(self.picked)
				self.update_from_preset.connect(widget.from_preset)
				parent.addWidget(widget)

		registerWidgets(leftLayout, [
			PickableImage('Basecolor', 'albedo', True),
			PickableImage('Roughness', 'roughness', True),
			PickableImage('Metallic', 'metallic', False),
			PickableImage('Bumpmap', 'normal', False),
			PickableImage('Heightmap', 'height', False),
			PickableImage('Ambient Occlusion', 'ao', False),
			PickableImage('Emission', 'emit', False)
		])

		#endregion
		''' ========================== RIGHT ========================== '''
		#region right

		rightLayout.addWidget(QLabel('Game'))

		self.gameDropdown = gameDropdown = QDataComboBox()
		rightLayout.addWidget(gameDropdown)
		for text,data in [
			('HL2: E2 / Portal / TF2', GameTarget.V2007),
			('Portal 2 / Alien Swarm', GameTarget.V2011),
			('Garry\'s Mod', GameTarget.VGMOD),
			('CS:GO / Strata', GameTarget.V2023),
		]: gameDropdown.addItem(text, data)
		gameDropdown.setCurrentData(Preset.game)

		def on_changed_game(x: int):
			self.backend.game = gameDropdown.itemData(x)
		gameDropdown.currentIndexChanged.connect(on_changed_game)

		rightLayout.addWidget(QLabel('Mode'))

		self.modeDropdown = modeDropdown = QDataComboBox()
		rightLayout.addWidget(modeDropdown)
		for text,data in [
			('Model: PBR', MaterialMode.PBRModel),
			('Model: Phong+Envmap', MaterialMode.PhongEnvmap),
			('Model: Phong+Envmap+Alpha', MaterialMode.PhongEnvmapAlpha),
			('Model: Phong+Envmap+Emission', MaterialMode.PhongEnvmapEmit),
			('Brush: PBR', MaterialMode.PBRBrush),
			('Brush: Envmap', MaterialMode.Envmap),
			('Brush: Envmap+Alpha', MaterialMode.EnvmapAlpha),
			('Brush: Envmap+Emission', MaterialMode.EnvmapEmit),
		]: modeDropdown.addItem(text, data)
		modeDropdown.setCurrentData(Preset.mode)
	
		def on_changed_mode(x: int):
			self.backend.mode = modeDropdown.itemData(x)
		modeDropdown.currentIndexChanged.connect(on_changed_mode)

		# rightLayout.addWidget(QLabel('Reflections'))

		# envmapDropdown = QDataComboBox()
		# rightLayout.addWidget(envmapDropdown)
		# for text,data in [
		# 	('None', None),
		# 	('Cubemap', 'env_cubemap'),
		# 	('(P2) Black Wall 002a', 'metal/black_wall_envmap_002a'),
		# 	('(CSGO) Generic Metal 01', 'environment maps/metal_generic_001'),
		# 	('(CSGO) Generic Metal 02', 'environment maps/metal_generic_002'),
		# 	('(CSGO) Generic Metal 03', 'environment maps/metal_generic_003'),
		# 	('(CSGO) Generic Metal 04', 'environment maps/metal_generic_004'),
		# 	('(CSGO) Generic Metal 05', 'environment maps/metal_generic_005'),
		# 	('(CSGO) Generic Metal 06', 'environment maps/metal_generic_006')
		# ]: envmapDropdown.addItem(text, data)
		# envmapDropdown.setCurrentData(Preset.envmap)

		# def on_changed_envmap(x: int):
		# 	self.backend.envmap = envmapDropdown.itemData(x)
		# envmapDropdown.currentIndexChanged.connect(on_changed_envmap)

		# rightLayout.addWidget(QLabel('Material Hint'))

		# self.hintDropdown = hintDropdown = QComboBox()
		# rightLayout.addWidget(hintDropdown)
		# for text,data in [
		# 	('None', None),
		# 	('Brick', 'brick'),
		# 	('Concrete', 'concrete'),
		# 	('Rock', 'rock'),
		# 	('Metal', 'metal'),
		# 	('Wood', 'wood'),
		# 	('Dirt', 'dirt'),
		# 	('Grass', 'grass'),
		# 	('Sand', 'sand'),
		# 	('Water', 'water'),
		# 	('Ice', 'ice'),
		# 	('Snow', 'snow'),
		# 	('Flesh', 'flesh'),
		# 	('Foliage', 'foliage'),
		# 	('Glass', 'glass'),
		# 	('Tile', 'tile'),
		# 	('Cardboard', 'cardboard'),
		# 	('Plaster', 'plaster'),
		# 	('Plastic', 'plastic'),
		# 	('Rubber', 'rubber'),
		# 	('Carpet', 'carpet'),
		# 	('Computer', 'computer'),
		# ]: hintDropdown.addItem(text, data)
		# hintDropdown.setCurrentData(None)

		#endregion
		''' ========================== FOOTER ========================== '''
		#region footer

		self.progressBar = QProgressBar()
		self.progressBar.setValue(0)
		self.progressBar.setMaximum(100)
		footer.addWidget(self.progressBar)

		self.exportButton = QPushButton('Export As...')
		self.exportButton.clicked.connect(self.export_as)
		footer.addWidget(self.exportButton)
		
		#endregion

	@Slot()
	def picked(self, kind: ImageRole, path: Path|None, set_icon):
		img = self.backend.pick(str(path) if path else None, kind)
		self.reset_watch()
		set_icon(img)

	def pick_target(self):
		print('Picking target')
		targetPath = QFileDialog.getSaveFileName(self, caption='Saving material...', filter='Valve Material (*.vmt)')[0]
		if len(targetPath): self.target = targetPath

	@Slot()
	def export(self):
		if self.exporting: return
		self.exporting = True
			
		print('Exporting...')
		self.exportButton.setEnabled(False)
		self.progressBar.setValue(0)
		QApplication.processEvents()

		try:
			material = self.backend.make_material(self.config.reloadOnExport)
			self.progressBar.setValue(50)

			if self.target == None: self.pick_target()
			if self.target == None: raise InterruptedError()
			
			targetPath: str = self.target # type: ignore

			self.backend.pick_vmt(targetPath)
			self.backend.export(material)
			self.progressBar.setValue(100)

			if self.config.hijackTarget:
				subprocess.Popen([self.config.hijackTarget, '-hijack', f'+mat_reloadmaterial {self.backend.name}'])
	
		except Exception as e:
			self.progressBar.setValue(0)

			if isinstance(e, InterruptedError):
				print('The export was cancelled by the user.')
			else:
				print('The export failed!\n\n', format_exc())
				message = QMessageBox(QMessageBox.Icon.Critical, 'Failed to export!', str(e))
				message.exec()
		
		finally:
			self.exportButton.setEnabled(True)
			self.exporting = False

	@Slot()
	def export_as(self):
		if self.exporting: return
		self.target = None
		self.export()

	@Slot()
	def watch(self):
		if not self.watching and self.target == None:
			self.pick_target()
			if not self.target: return

		self.watching = not self.watching
		if self.watching:	self.start_watch()
		else:				self.stop_watch()
		print('Watching:', self.watcher.files())
	
	def start_watch(self):
		self.watchAction.setText('Stop Watching')
		paths = [x for x in [
			self.backend.albedoPath,
			self.backend.roughnessPath,
			self.backend.metallicPath,
			self.backend.emitPath,
			self.backend.aoPath,
			self.backend.normalPath,
			self.backend.heightPath,
		] if x != None]
		self.watcher.addPaths(paths)

	def stop_watch(self):
		self.watchAction.setText('Watch')
		self.watcher.removePaths(self.watcher.files())

	def reset_watch(self):
		if not self.watching: return
		print('Resetting watch...')
		self.stop_watch()
		self.start_watch()
		print('Watching:', self.watcher.files())

	def force_stop_watch(self, issue: str='An error occurred!'):
		if not self.watching: return
		message = QMessageBox(QMessageBox.Icon.Information, title='Watch Error', text=issue)
		message.exec()

	@Slot()
	def on_file_changed(self, file: str):
		assert self.watching, 'SOMETHING HAS GONE VERY WRONG HERE!!'
		print('File changed:', file)
		self.watcherCooldown.start(500)

	@Slot()
	def load_preset(self):
		selected = QFileDialog.getOpenFileName(self, caption='Loading preset...', filter='JSON Presets (*.json)')[0]
		if not len(selected): return

		# Reset target path
		self.target = None

		preset = Preset.load(selected)
		self.gameDropdown.setCurrentData(preset.game)
		self.modeDropdown.setCurrentData(preset.mode)
		# self.hintDropdown.setCurrentData(preset.hint)
		# self.envmapDropdown.setCurrentData(preset.envmap)
		self.update_from_preset.emit(preset)
	
	def save_preset(self):
		selected = QFileDialog.getSaveFileName(self, caption='Saving preset...', filter='JSON Presets (*.json)')[0]
		if not len(selected): return

		# Reset target path
		self.target = None
		
		preset = Preset()
		self.backend.save_preset(preset)
		preset.save(selected)

def start_gui():
	app: QApplication = QApplication()
	app_config = load_config()

	if '--style-fusion' in argv: app_config.appTheme = AppTheme.Fusion
	if '--style-native' in argv: app_config.appTheme = AppTheme.Native

	match app_config.appTheme:
		case AppTheme.Default:
			app.setStyle( 'Fusion' )
			app.setFont( 'Inter' )
			app.setStyleSheet( STYLESHEET )
		case AppTheme.Fusion:
			app.setStyle( 'Fusion' )
			app.setStyleSheet( STYLESHEET_MIN )

	win = MainWindow( app_config )
	win.show()
	app.exec()

if __name__ == '__main__':
	start_gui()
