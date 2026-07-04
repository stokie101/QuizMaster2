"""
QuizMaster Splash Screen with Video
Shows opening splash.mp4 video before main window
"""

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPalette, QColor
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QWidget, QVBoxLayout


class QuizMasterSplashScreen(QWidget):
    """Video splash screen for QuizMaster"""

    def __init__(self, video_path: Path, parent=None):
        super().__init__(parent)
        self.logger = logging.getLogger(self.__class__.__name__)
        self.video_path = video_path

        self._setup_ui()
        self._setup_player()

    def _setup_ui(self):
        """Setup the splash window"""
        self.setWindowFlags(
            Qt.WindowType.SplashScreen |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        # Set black background
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(10, 10, 10))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        # Set size (adjust to your video dimensions)
        self.setFixedSize(1280, 720)

        # Center on screen
        screen_geometry = self.screen().geometry()
        x = (screen_geometry.width() - self.width()) // 2
        y = (screen_geometry.height() - self.height()) // 2
        self.move(x, y)

        # Video widget
        self.video_widget = QVideoWidget()
        self.video_widget.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)

        # Layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.video_widget)
        self.setLayout(layout)

    def _setup_player(self):
        """Setup media player"""
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)

        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)

        # Set video source
        from PySide6.QtCore import QUrl
        video_url = QUrl.fromLocalFile(str(self.video_path))
        self.player.setSource(video_url)

        # Connect signals
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.player.errorOccurred.connect(self._on_error)

    def _on_media_status_changed(self, status):
        """Handle media status changes"""
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.logger.info("Splash video finished")
            self.close()
        elif status == QMediaPlayer.MediaStatus.LoadedMedia:
            self.logger.info("Splash video loaded")

    def _on_error(self, error):
        """Handle playback errors"""
        self.logger.error(f"Splash video error: {error} - {self.player.errorString()}")
        # Close splash after short delay if error occurs
        QTimer.singleShot(1000, self.close)

    def show_and_play(self):
        """Show splash and start video playback"""
        self.show()
        self.player.play()
        self.logger.info(f"Playing splash video: {self.video_path}")

    def skip_splash(self):
        """Allow user to skip splash (ESC key)"""
        self.player.stop()
        self.close()

    def keyPressEvent(self, event):
        """Handle key presses - ESC to skip"""
        if event.key() == Qt.Key.Key_Escape:
            self.skip_splash()
        else:
            super().keyPressEvent(event)
