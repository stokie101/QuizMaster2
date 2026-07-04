"""
Previous Leaderboard Window

This module provides a dialog for displaying previous leaderboard data.
"""

import json
import logging
import os
from datetime import datetime
from typing import Dict, Any, List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QComboBox, QMessageBox
)

from core.quiz.leaderboard.leaderboard_utils import LeaderboardUtils
from core.services.service_locator import ServiceLocator


def create_neon_blue_text(text, size=12, glow_intensity='medium'):
    """Create text with neon blue glow effect"""
    glow_effects = {
        'light': '0 0 5px #00FFFF',
        'medium': '0 0 10px #00FFFF, 0 0 20px #00AAFF',
        'heavy': '0 0 15px #00FFFF, 0 0 25px #00AAFF, 0 0 35px #0088FF'
    }

    return f"""
        color: #00FFFF;
        font-size: {size}px;
        font-weight: bold;
        text-shadow: {glow_effects.get(glow_intensity, glow_effects['medium'])};
        background: transparent;
        padding: 5px;
    """


# Neon blue color constants
NEON_BLUE_COLORS = {
    'primary': '#00AAFF',
    'secondary': '#0088FF',
    'accent': '#00FFFF',
    'glow': '#00CCFF',
    'dark': '#001133',
    'transparent': 'rgba(0, 170, 255, 0.3)'
}


class LeaderboardRowDisplay(QWidget):
    """Widget representing a row in the previous leaderboard display."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()
        self._apply_clean_styling()

    def _init_ui(self):
        """Initialize UI components"""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 12, 20, 12)
        layout.setSpacing(20)

        # Rank
        self.rank_label = QLabel("--")
        self.rank_label.setFixedWidth(80)
        self.rank_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.rank_label)

        # Player name
        self.name_label = QLabel("--")
        self.name_label.setFixedWidth(200)
        layout.addWidget(self.name_label)

        # Correct answers
        self.correct_label = QLabel("--")
        self.correct_label.setFixedWidth(100)
        self.correct_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.correct_label)

        # Incorrect answers
        self.incorrect_label = QLabel("--")
        self.incorrect_label.setFixedWidth(100)
        self.incorrect_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.incorrect_label)

        # PUT THE STRETCH HERE (same as header)
        layout.addStretch()

        # Score - now it matches the header position
        self.score_label = QLabel("--")
        self.score_label.setFixedWidth(90)
        self.score_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.score_label)

    def _apply_clean_styling(self):
        """Apply clean professional styling to the row"""
        row_style = f"""
            LeaderboardRowDisplay {{
                background: rgba(0, 170, 255, 0.08);
                border: none;
                border-bottom: 1px solid rgba(0, 170, 255, 0.2);
                margin: 0px;
                padding: 0px;
            }}
            
            LeaderboardRowDisplay:hover {{
                background: rgba(0, 170, 255, 0.15);
            }}
            
            QLabel {{
                color: #00FFFF;
                font-weight: bold;
                text-shadow: 0 0 3px #00FFFF;
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
            }}
        """
        self.setStyleSheet(row_style)

    def update_data(self, position: int, user_data: Dict):
        """Update row with user data"""
        try:
            # Special styling for top 3 positions
            rank_text = f"#{position}"
            if position == 1:
                rank_text = "🥇 #1"
                self.rank_label.setStyleSheet("color: #FFD700; text-shadow: 0 0 8px #FFD700; font-weight: bold;")
            elif position == 2:
                rank_text = "🥈 #2"
                self.rank_label.setStyleSheet("color: #C0C0C0; text-shadow: 0 0 8px #C0C0C0; font-weight: bold;")
            elif position == 3:
                rank_text = "🥉 #3"
                self.rank_label.setStyleSheet("color: #CD7F32; text-shadow: 0 0 8px #CD7F32; font-weight: bold;")
            else:
                self.rank_label.setStyleSheet("color: #00FFFF; text-shadow: 0 0 3px #00FFFF; font-weight: bold;")

            self.rank_label.setText(rank_text)

            # Display name - try different possible keys
            display_name = user_data.get("display_name", user_data.get("username", "--"))
            self.name_label.setText(display_name)

            # Correct answers
            correct = user_data.get("correct_answers", 0)
            self.correct_label.setText(str(correct))

            # Incorrect answers
            incorrect = user_data.get("incorrect_answers", 0)
            self.incorrect_label.setText(str(incorrect))

            # Calculate score if not present
            score = user_data.get("score", 0)
            if score == 0 and correct > 0:
                # If score is 0 but there are correct answers, use correct answers as score
                score = correct
            self.score_label.setText(str(score))

        except Exception as e:
            logging.error(f"Error updating leaderboard row: {e}")


class PreviousLeaderboardWindow(QDialog):
    """Dialog for displaying previous leaderboard data."""

    def __init__(self, parent=None, session_data: Dict[str, Any] = None):
        """
        Initialize the previous leaderboard window.

        Args:
            parent: The parent widget
            session_data: The session data (optional)
        """
        super().__init__(parent)

        # Initialize services and data
        self.theme_applicator = ServiceLocator.get_instance().get_service("ThemeApplicator")
        self.leaderboard_utils = LeaderboardUtils()
        self.session_data = session_data or {}
        self.leaderboard_data = {}
        self.available_sessions = []

        # Initialize logger
        self.logger = logging.getLogger("PreviousLeaderboardWindow")

        # Set up UI
        self._setup_ui()
        self._load_available_sessions()

    def _setup_ui(self):
        """Set up the user interface."""
        try:
            # Set window properties
            self.setWindowTitle("Previous Leaderboards")
            self.setMinimumSize(800, 600)
            self.resize(1000, 700)

            # Apply background styling
            self._apply_background_styling()

            # Main layout
            main_layout = QVBoxLayout(self)
            main_layout.setContentsMargins(30, 30, 30, 30)
            main_layout.setSpacing(25)

            # Header section
            self._create_header_section(main_layout)

            # Session selector
            self._create_session_selector(main_layout)

            # Stats section
            self._create_stats_section(main_layout)

            # Leaderboard section
            self._create_leaderboard_section(main_layout)

            # Button section
            self._create_button_section(main_layout)

        except Exception as e:
            logging.error(e, "PreviousLeaderboardWindow._setup_ui", "ERROR")
            self._create_minimal_ui()

    def _apply_background_styling(self):
        """Apply clean background styling"""
        try:
            # Get theme applicator for background image
            theme_applicator = ServiceLocator.get_instance().get_service("ThemeApplicator")

            background_style = f"""
                PreviousLeaderboardWindow {{
                    background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 1,
                                                stop: 0 rgba(20, 20, 40, 0.95),
                                                stop: 1 rgba(40, 40, 80, 0.95));
                    color: #FFFFFF;
                }}
            """

            # Try to get background image from theme
            if theme_applicator and hasattr(theme_applicator, 'get_background_image'):
                try:
                    bg_image = theme_applicator.get_background_image()
                    if bg_image and os.path.exists(bg_image):
                        background_style = f"""
                            PreviousLeaderboardWindow {{
                                background-image: url({bg_image});
                                background-repeat: no-repeat;
                                background-position: center;
                                background-attachment: fixed;
                                color: #FFFFFF;
                            }}
                        """
                except Exception as e:
                    self.logger.debug(f"Could not get background image: {e}")

            self.setStyleSheet(background_style)

        except Exception as e:
            self.logger.error(f"Error applying background styling: {e}")

    @staticmethod
    def _create_header_section(layout: QVBoxLayout):
        """Create the header section."""
        try:
            # Create header
            header = QLabel("🏆 PREVIOUS LEADERBOARDS")
            header.setAlignment(Qt.AlignmentFlag.AlignCenter)
            header.setStyleSheet(create_neon_blue_text("", size=28, glow_intensity='heavy'))
            header.setContentsMargins(0, 0, 0, 20)
            layout.addWidget(header)

        except Exception as e:
            logging.error(e, "PreviousLeaderboardWindow._create_header_section", "ERROR")

    def _create_session_selector(self, layout: QVBoxLayout):
        """Create session selector dropdown."""
        try:
            # Label
            selector_label = QLabel("Select Session:")
            selector_label.setStyleSheet(create_neon_blue_text("", size=16))
            selector_label.setContentsMargins(0, 0, 0, 8)
            layout.addWidget(selector_label)

            # Dropdown
            self.session_combo = QComboBox()
            self.session_combo.setStyleSheet(f"""
                QComboBox {{
                    background: rgba(0, 170, 255, 0.1);
                    border: 2px solid {NEON_BLUE_COLORS['primary']};
                    border-radius: 8px;
                    padding: 12px;
                    color: #00FFFF;
                    font-weight: bold;
                    font-size: 14px;
                    min-height: 20px;
                }}
                QComboBox:hover {{
                    background: rgba(0, 170, 255, 0.2);
                    border-color: {NEON_BLUE_COLORS['accent']};
                }}
                QComboBox::drop-down {{
                    border: none;
                    width: 30px;
                }}
                QComboBox::down-arrow {{
                    image: none;
                    border-left: 6px solid transparent;
                    border-right: 6px solid transparent;
                    border-top: 6px solid #00FFFF;
                }}
                QComboBox QAbstractItemView {{
                    background: rgba(20, 20, 40, 0.95);
                    border: 2px solid {NEON_BLUE_COLORS['primary']};
                    color: #00FFFF;
                }}
                QComboBox QAbstractItemView::item {{
                    background: transparent;
                    color: #00FFFF;
                }}
                QComboBox QAbstractItemView::item:hover {{
                    background: rgba(0, 170, 255, 0.18);
                    color: #FFFFFF;
                }}
                QComboBox QAbstractItemView::item:selected {{
                    background: rgba(0, 170, 255, 0.3);
                    color: #FFFFFF;
                }}
            """)
            self.session_combo.currentTextChanged.connect(self._on_session_selected)
            layout.addWidget(self.session_combo)

        except Exception as e:
            logging.error(e, "PreviousLeaderboardWindow._create_session_selector", "ERROR")

    def _create_stats_section(self, layout: QVBoxLayout):
        """Create the stats section."""
        try:
            # Create stats container
            self.stats_container = QHBoxLayout()
            self.stats_container.setSpacing(30)
            self.stats_container.setContentsMargins(0, 20, 0, 20)

            # Add to main layout
            layout.addLayout(self.stats_container)

        except Exception as e:
            logging.error(e, "PreviousLeaderboardWindow._create_stats_section", "ERROR")

    def _update_stats_display(self):
        """Update the stats display with current session data."""
        try:
            # Clear existing stats
            while self.stats_container.count():
                child = self.stats_container.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()

            # Create stat widgets based on available data
            stats_config = [
                ("Total Players", len(self.leaderboard_data), "👥"),
                ("Session Date", self._format_session_date(), "📅"),
                ("Questions", self.session_data.get("total_questions", "N/A"), "❓"),
                ("Duration", self.session_data.get("duration_formatted", "N/A"), "⏱️")
            ]

            for label, value, icon in stats_config:
                stat_widget = self._create_stat_widget(f"{icon} {label}", str(value))
                self.stats_container.addWidget(stat_widget)

            # Add stretch to center the stats
            self.stats_container.addStretch()

        except Exception as e:
            logging.error(e, "PreviousLeaderboardWindow._update_stats_display", "ERROR")

    def _format_session_date(self):
        """Format session date from filename or session data."""
        try:
            # Try to get from session data first
            if 'session_start_time' in self.session_data:
                return self.session_data['session_start_time']

            # Try to parse from current combo selection
            current_file = self.session_combo.currentText()
            if current_file and '_' in current_file:
                try:
                    # Extract timestamp from filename like "S1_20231201_143022.json"
                    parts = current_file.replace('.json', '').split('_')
                    if len(parts) >= 3:
                        date_str = parts[1]
                        time_str = parts[2]
                        # Parse date and time
                        dt = datetime.strptime(f"{date_str}_{time_str}", "%Y%m%d_%H%M%S")
                        return dt.strftime("%Y-%m-%d %H:%M:%S")
                except:
                    pass

            return "Unknown"
        except Exception as e:
            self.logger.error(f"Error formatting session date: {e}")
            return "Unknown"

    @staticmethod
    def _create_stat_widget(label: str, value: str) -> QWidget:
        """Create a clean stat widget."""
        try:
            widget = QWidget()
            widget.setStyleSheet(f"""
                QWidget {{
                    background: rgba(0, 170, 255, 0.1);
                    border: 1px solid rgba(0, 170, 255, 0.3);
                    border-radius: 12px;
                    padding: 0px;
                    margin: 0px;
                }}
            """)

            layout = QVBoxLayout(widget)
            layout.setContentsMargins(20, 15, 20, 15)
            layout.setSpacing(8)

            # Label
            label_widget = QLabel(label)
            label_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label_widget.setStyleSheet(f"""
                color: #00FFFF;
                font-size: 13px;
                font-weight: bold;
                text-shadow: 0 0 3px #00FFFF;
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
            """)
            layout.addWidget(label_widget)

            # Value
            value_widget = QLabel(value)
            value_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            value_widget.setStyleSheet(f"""
                color: #FFFFFF;
                font-size: 18px;
                font-weight: bold;
                text-shadow: 0 0 5px #00FFFF;
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
            """)
            layout.addWidget(value_widget)

            return widget
        except Exception as e:
            logging.error(e, "PreviousLeaderboardWindow._create_stat_widget", "ERROR")
            return QWidget()

    def _create_leaderboard_section(self, layout: QVBoxLayout):
        """Create the leaderboard section with clean styling."""
        try:
            # Create header row
            header_row = self._create_header_row()
            layout.addWidget(header_row)

            # Create scroll area with clean styling
            self.scroll = QScrollArea()
            self.scroll.setWidgetResizable(True)
            self.scroll.setStyleSheet(f"""
                QScrollArea {{
                    background: rgba(0, 0, 0, 0.3);
                    border: 2px solid {NEON_BLUE_COLORS['primary']};
                    border-radius: 12px;
                    border-top: none;
                    border-top-left-radius: 0px;
                    border-top-right-radius: 0px;
                }}
                QScrollBar:vertical {{
                    background: rgba(0, 170, 255, 0.1);
                    width: 12px;
                    border-radius: 6px;
                    margin: 0px;
                }}
                QScrollBar::handle:vertical {{
                    background: {NEON_BLUE_COLORS['primary']};
                    border-radius: 6px;
                    min-height: 20px;
                }}
                QScrollBar::handle:vertical:hover {{
                    background: {NEON_BLUE_COLORS['accent']};
                }}
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                    height: 0px;
                }}
            """)

            # Setup scroll content
            self.scroll_content = QWidget()
            self.scroll_content.setStyleSheet("background: transparent;")
            self.scroll_layout = QVBoxLayout(self.scroll_content)
            self.scroll_layout.setSpacing(0)
            self.scroll_layout.setContentsMargins(0, 0, 0, 0)

            # Add empty state label
            self.empty_label = QLabel("Select a session to view leaderboard")
            self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.empty_label.setStyleSheet(f"""
                QLabel {{
                    color: {NEON_BLUE_COLORS['accent']};
                    font-size: 18px;
                    font-style: italic;
                    text-shadow: 0 0 8px {NEON_BLUE_COLORS['accent']};
                    padding: 40px;
                    background: transparent;
                    border: none;
                }}
            """)
            self.scroll_layout.addWidget(self.empty_label)

            self.scroll.setWidget(self.scroll_content)
            layout.addWidget(self.scroll)

        except Exception as e:
            logging.error(e, "PreviousLeaderboardWindow._create_leaderboard_section", "ERROR")

    @staticmethod
    def _create_header_row():
        """Create header row for the leaderboard."""
        header_widget = QWidget()
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(20, 15, 20, 15)
        header_layout.setSpacing(20)

        # Headers - separated to add stretch in the right place
        headers_before_stretch = [
            ("🏅 RANK", 80),
            ("👤 PLAYER", 200),
            ("✅ CORRECT", 100),
            ("❌ INCORRECT", 100),
        ]

        for text, width in headers_before_stretch:
            header_label = QLabel(text)
            header_label.setFixedWidth(width)
            header_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            header_label.setStyleSheet(f"""
                    color: #FFFFFF;
                    font-size: 14px;
                    font-weight: bold;
                    text-shadow: 0 0 5px #00FFFF;
                    background: transparent;
                    border: none;
                    padding: 0px;
                    margin: 0px;
                """)
            header_layout.addWidget(header_label)

        # Add stretch BEFORE score to match the row layout
        header_layout.addStretch()

        # Add score header last
        score_header = QLabel("⭐ SCORE")
        score_header.setFixedWidth(90)
        score_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        score_header.setStyleSheet(f"""
                color: #FFFFFF;
                font-size: 14px;
                font-weight: bold;
                text-shadow: 0 0 5px #00FFFF;
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
            """)
        header_layout.addWidget(score_header)
        # Style the header container
        header_widget.setStyleSheet(f"""
            QWidget {{
                background: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 0,
                                            stop: 0 rgba(0, 170, 255, 0.25),
                                            stop: 1 rgba(0, 170, 255, 0.15));
                border: 2px solid {NEON_BLUE_COLORS['primary']};
                border-radius: 12px;
                border-bottom-left-radius: 0px;
                border-bottom-right-radius: 0px;
                margin: 0px;
                padding: 0px;
            }}
        """)

        return header_widget

    def _create_button_section(self, layout: QVBoxLayout):
        """Create the button section."""
        try:
            # Create button container
            button_container = QHBoxLayout()
            button_container.setContentsMargins(0, 20, 0, 0)

            # Add spacer to push buttons to the right
            button_container.addStretch()

            # Create close button
            close_button = QPushButton("Close")
            close_button.setStyleSheet(f"""
                QPushButton {{
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                                stop:0 {NEON_BLUE_COLORS['primary']}, 
                                stop:1 {NEON_BLUE_COLORS['secondary']});
                    border: 2px solid {NEON_BLUE_COLORS['accent']};
                    border-radius: 10px;
                    padding: 12px 30px;
                    color: white;
                    font-weight: bold;
                    font-size: 14px;
                }}
                QPushButton:hover {{
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                                stop:0 {NEON_BLUE_COLORS['accent']}, 
                                stop:1 {NEON_BLUE_COLORS['primary']});
                    text-shadow: 0 0 5px #FFFFFF;
                }}
                QPushButton:pressed {{
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, 
                                stop:0 {NEON_BLUE_COLORS['secondary']}, 
                                stop:1 {NEON_BLUE_COLORS['primary']});
                }}
            """)
            close_button.clicked.connect(self.accept)
            button_container.addWidget(close_button)

            layout.addLayout(button_container)

        except Exception as e:
            logging.error(e, "PreviousLeaderboardWindow._create_button_section", "ERROR")

    def _load_available_sessions(self):
        """Load available session files."""
        try:
            session_files = self.leaderboard_utils.get_session_files()
            self.available_sessions = session_files

            # Populate combo box
            self.session_combo.clear()
            if session_files:
                self.session_combo.addItems(session_files)
                self.logger.info(f"Loaded {len(session_files)} session files")
            else:
                self.session_combo.addItem("No sessions found")
                self.logger.info("No session files found")

        except Exception as e:
            logging.error(e, "PreviousLeaderboardWindow._load_available_sessions", "ERROR")

    def _on_session_selected(self, filename: str):
        """Handle session selection."""
        try:
            if not filename or filename == "No sessions found":
                return

            # Load the selected session
            file_path = os.path.join(self.leaderboard_utils.leaderboard_dir, filename)
            self.load_leaderboard(file_path)

        except Exception as e:
            logging.error(e, "PreviousLeaderboardWindow._on_session_selected", "ERROR")

    def load_leaderboard(self, file_path: str):
        """Load leaderboard data from a file."""
        try:
            if not os.path.exists(file_path):
                QMessageBox.warning(self, "File Not Found", f"File not found: {file_path}")
                return

            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Extract leaderboard and session data
            if isinstance(data, dict):
                # Check for different possible structures in the saved file
                if 'leaderboard' in data:
                    # New format - data is in 'leaderboard' key
                    self.leaderboard_data = data['leaderboard']
                    self.session_data = {
                        'session_number': data.get('session_number', 0),
                        'session_date': data.get('session_date', ''),
                        'total_questions': data.get('total_questions', 0),
                        'duration_formatted': data.get('session_stats', {}).get('duration_formatted', '00:00:00')
                    }
                elif 'leaderboard_data' in data:
                    # Alternative format
                    self.leaderboard_data = data['leaderboard_data']
                    self.session_data = data.get('session_data', {})
                else:
                    # Assume the whole file is leaderboard data
                    self.leaderboard_data = data
                    self.session_data = {}
            else:
                self.leaderboard_data = {}
                self.session_data = {}

            # Debug log what we loaded
            self.logger.info(f"Loaded leaderboard with {len(self.leaderboard_data)} users")
            if self.leaderboard_data:
                # Log a sample user to debug
                sample_user_id = next(iter(self.leaderboard_data))
                sample_user = self.leaderboard_data[sample_user_id]
                self.logger.info(f"Sample user data: {sample_user}")

            # Update displays
            self._update_stats_display()
            self._update_leaderboard_display()

            self.logger.info(f"Loaded leaderboard from: {file_path}")

        except Exception as e:
            logging.error(e, "PreviousLeaderboardWindow.load_leaderboard", "ERROR")
            QMessageBox.critical(self, "Error", f"Failed to load leaderboard: {str(e)}")

    def _update_leaderboard_display(self):
        """Update the leaderboard display with loaded data."""
        try:
            # Clear existing rows
            while self.scroll_layout.count() > 1:  # Keep empty label
                item = self.scroll_layout.takeAt(1)
                if item.widget():
                    item.widget().deleteLater()

            if not self.leaderboard_data:
                self.empty_label.setText("No leaderboard data in selected session")
                self.empty_label.show()
                return

            # Hide empty label
            self.empty_label.hide()

            # Sort leaderboard data
            sorted_data = []

            # Handle different data structures
            for user_id, user_data in self.leaderboard_data.items():
                if isinstance(user_data, dict):
                    # Get score - try different possible keys
                    score = 0
                    if "score" in user_data:
                        score = user_data.get("score", 0)
                    elif "correct_answers" in user_data:
                        score = user_data.get("correct_answers", 0)

                    # Ensure we have all required fields
                    if "display_name" not in user_data and "username" in user_data:
                        user_data["display_name"] = user_data["username"]

                    if "correct_answers" not in user_data:
                        user_data["correct_answers"] = 0

                    if "incorrect_answers" not in user_data:
                        user_data["incorrect_answers"] = 0

                    sorted_data.append((user_id, user_data, score))

            # Sort by score
            sorted_data.sort(key=lambda x: x[2], reverse=True)

            # Add leaderboard rows
            for position, (user_id, user_data, score) in enumerate(sorted_data, 1):
                row = LeaderboardRowDisplay()
                row.update_data(position, user_data)
                self.scroll_layout.addWidget(row)

            # Add stretch at the end
            self.scroll_layout.addStretch()

        except Exception as e:
            logging.error(e, "PreviousLeaderboardWindow._update_leaderboard_display", "ERROR")

    def load_leaderboards(self, file_paths: List[str]):
        """Load multiple leaderboard files (for compatibility)."""
        try:
            if file_paths:
                # Load the first file by default
                self.load_leaderboard(file_paths[0])
        except Exception as e:
            logging.error(e, "PreviousLeaderboardWindow.load_leaderboards", "ERROR")

    def _create_minimal_ui(self):
        """Create a minimal UI when normal setup fails."""
        try:
            # Clear layout
            if self.layout():
                while self.layout().count():
                    child = self.layout().takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()

            # Create minimal layout
            minimal_layout = QVBoxLayout(self)

            # Error message
            error_label = QLabel("Error loading leaderboard interface")
            error_label.setStyleSheet("color: red; font-size: 16px;")
            error_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            minimal_layout.addWidget(error_label)

            # Close button
            close_button = QPushButton("Close")
            close_button.clicked.connect(self.accept)
            minimal_layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignCenter)

        except Exception as e:
            logging.error(e, "PreviousLeaderboardWindow._create_minimal_ui", "ERROR")
