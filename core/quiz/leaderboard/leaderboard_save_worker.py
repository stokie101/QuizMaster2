import logging
import multiprocessing as mp
import threading
from typing import Dict, Any, Optional

from PySide6.QtCore import QObject, Signal


class SaveProcessController(QObject):
    """
    Orchestrates a separate process to save leaderboard data.
    Emits: started(), completed(success: bool, message: str)
    No progress events by design (per your preference).
    """
    started = Signal()
    completed = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self._proc: Optional[mp.Process] = None
        self._stop_ev: Optional[mp.Event] = None
        self._conn_parent, self._conn_child = None, None
        self._monitor_thread: Optional[threading.Thread] = None

    def start(self, payload: Dict[str, Any]):
        """
        payload keys expected:
          - leaderboard_data: dict
          - session_number: int
          - leaderboard_dir: str
          - avatars_dir: str (kept for parity/future, not used)
          - session_stats: dict
          - previous_session_stats: dict or None
          - chat_participants: list[str]
          - quiz_participants: list[str]
          - question_history: dict
        """
        if self._proc is not None:
            self.completed.emit(False, "Save already in progress")
            return

        ctx = mp.get_context("spawn")
        self._stop_ev = ctx.Event()
        self._conn_parent, self._conn_child = ctx.Pipe(duplex=False)

        self._proc = ctx.Process(
            target=_save_worker_main,
            name="LeaderboardSaveWorker",
            args=(self._stop_ev, self._conn_child, payload),
            daemon=True
        )
        try:
            self._proc.start()
        except Exception as e:
            self._cleanup_handles()
            self.completed.emit(False, f"Could not start save process: {e}")
            return

        self.started.emit()
        # Monitor results in a tiny thread
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def _monitor_loop(self):
        msg = None
        success = False
        try:
            # Wait for a single message from the worker or process exit
            if self._conn_parent.poll(300):  # generous timeout
                result = self._conn_parent.recv()
                success = bool(result.get("success"))
                msg = str(result.get("message", ""))
            else:
                success = False
                msg = "Save process timed out"
        except EOFError:
            success = False
            msg = "Save process ended unexpectedly"
        except Exception as e:
            success = False
            msg = f"Save monitor error: {e}"
        finally:
            self._cleanup_handles()
            self.completed.emit(success, msg)

    def _cleanup_handles(self):
        try:
            if self._stop_ev:
                self._stop_ev.set()
        except Exception:
            pass
        if self._proc is not None:
            try:
                self._proc.join(timeout=3.0)
                if self._proc.is_alive():
                    self._proc.terminate()
                    self._proc.join(timeout=2.0)
            except Exception:
                pass
        self._proc = None
        try:
            if self._conn_parent:
                self._conn_parent.close()
        except Exception:
            pass
        try:
            if self._conn_child:
                self._conn_child.close()
        except Exception:
            pass
        self._conn_parent = None
        self._conn_child = None
        self._stop_ev = None


def _save_worker_main(stop_ev: mp.Event, conn, payload: Dict[str, Any]):
    """
    Runs in a separate process. No Qt imports here.
    Writes a single JSON snapshot using LeaderboardUtils helpers.
    Sends back one result via Pipe, then exits.
    """
    try:
        import os
        from datetime import datetime
        from core.quiz.leaderboard.leaderboard_utils import (
            LeaderboardUtils,
            calculate_comparison_metrics,
            calculate_participation_stats
        )

        utils = LeaderboardUtils()
        leaderboard_dir = payload.get("leaderboard_dir") or utils.leaderboard_dir

        leaderboard_data = payload.get("leaderboard_data") or {}
        session_number = int(payload.get("session_number") or 1)
        session_stats = payload.get("session_stats") or {}
        prev_session_stats = payload.get("previous_session_stats") or None
        chat_participants = payload.get("chat_participants") or []
        quiz_participants = payload.get("quiz_participants") or []
        question_history = payload.get("question_history") or {}

        # Calculate derived stats in worker (Qt-free)
        comparison_stats = calculate_comparison_metrics(session_stats, prev_session_stats)
        peak_viewers = session_stats.get("peak_viewers", 0)
        participation_stats = calculate_participation_stats(chat_participants, quiz_participants, peak_viewers)

        # Make serializable copy (Qt-free)
        serializable_leaderboard = utils.create_serializable_leaderboard(leaderboard_data, session_number)

        # Build save payload
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"S{session_number}_{timestamp}.json"
        save_data = {
            "session_number": session_number,
            "leaderboard": serializable_leaderboard,
            "session_stats": session_stats,
            "participation": participation_stats,
            "comparison": comparison_stats,
            "question_history": question_history,
            "session_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_questions": len(question_history),
            "total_players": len(leaderboard_data),
            "quiz_participants": len(quiz_participants),
            "chat_participants": len(chat_participants),
        }

        # Atomic write
        path = utils.safe_write_json(save_data, leaderboard_dir, filename)
        conn.send({"success": True, "message": f"Saved leaderboard: {os.path.basename(path)}"})

    except Exception as e:
        import traceback
        logging.error(f"Save worker error: {e}")
        logging.error(traceback.format_exc())
        try:
            conn.send({"success": False, "message": f"Save failed: {e}"})
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass
