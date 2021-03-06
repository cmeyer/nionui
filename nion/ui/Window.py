"""
A basic class to serve as the document controller of a typical one window application.
"""

# standard libraries
import asyncio
import concurrent.futures
import gettext
import logging
import typing

# local libraries
from nion.utils import Process
from nion.ui import UserInterface


_ = gettext.gettext


class Window:

    def __init__(self, ui: UserInterface.UserInterface, app=None, window_style=None, persistent_id=None):
        self.ui = ui
        self.app = app
        self.__document_window = self.ui.create_document_window()
        if window_style:
            self.__document_window.window_style = window_style
        self.__persistent_id = persistent_id
        self.__shown = False
        self.__document_window.on_periodic = self.periodic
        self.__document_window.on_queue_task = self.queue_task
        self.__document_window.on_add_task = self.add_task
        self.__document_window.on_clear_task = self.clear_task
        self.__document_window.on_about_to_show = self.about_to_show
        self.__document_window.on_about_to_close = self.about_to_close
        self.__document_window.on_activation_changed = self.activation_changed
        self.__document_window.on_size_changed = self.size_changed
        self.__document_window.on_position_changed = self.position_changed
        self.__document_window.on_refocus_widget = self.refocus_widget
        self.__periodic_queue = Process.TaskQueue()
        self.__periodic_set = Process.TaskSet()
        # configure the event loop object
        logger = logging.getLogger()
        old_level = logger.level
        logger.setLevel(logging.INFO)
        self.__event_loop = asyncio.new_event_loop()  # outputs a debugger message!
        logger.setLevel(old_level)

    def close(self):
        # give cancelled tasks a chance to finish
        self.__event_loop.stop()
        self.__event_loop.run_forever()
        try:
            # this assumes that all outstanding tasks finish in a reasonable time (i.e. no infinite loops).
            self.__event_loop.run_until_complete(asyncio.gather(*asyncio.Task.all_tasks(loop=self.__event_loop), loop=self.__event_loop))
        except concurrent.futures.CancelledError:
            pass
        # now close
        # due to a bug in Python libraries, the default executor needs to be shutdown explicitly before the event loop
        # see http://bugs.python.org/issue28464
        if self.__event_loop._default_executor:
            self.__event_loop._default_executor.shutdown()
        self.__event_loop.close()
        self.__event_loop = None
        self.ui.destroy_document_window(self.__document_window)
        self.__document_window = None
        self.__periodic_queue = None
        self.__periodic_set = None

    @property
    def _document_window(self):
        # for testing only
        return self.__document_window

    def _create_menus(self):
        self._file_menu = self.add_menu(_("File"))
        self._edit_menu = self.add_menu(_("Edit"))
        self._window_menu = self.add_menu(_("Window"))
        self._help_menu = self.add_menu(_("Help"))

        self._close_action = self._file_menu.add_menu_item(_("Close Window"), self.request_close, key_sequence="close")
        self._file_menu.add_separator()
        self._page_setup_action = self._file_menu.add_menu_item(_("Page Setup"), self._page_setup)
        self._print_action = self._file_menu.add_menu_item(_("Print"), self._print, key_sequence="Ctrl+P")
        self._file_menu.add_separator()
        self._quit_action = self._file_menu.add_menu_item(_("Exit"), self._request_exit, key_sequence="quit", role="quit")

        self._undo_action = self._edit_menu.add_menu_item(_("Undo"), self._undo, key_sequence="undo")
        self._redo_action = self._edit_menu.add_menu_item(_("Redo"), self._redo, key_sequence="redo")
        self._edit_menu.add_separator()
        self._cut_action = self._edit_menu.add_menu_item(_("Cut"), self._cut, key_sequence="cut")
        self._copy_action = self._edit_menu.add_menu_item(_("Copy"), self._copy, key_sequence="copy")
        self._paste_action = self._edit_menu.add_menu_item(_("Paste"), self._paste, key_sequence="paste")
        self._delete_action = self._edit_menu.add_menu_item(_("Delete"), self._delete, key_sequence="delete")
        self._select_all_action = self._edit_menu.add_menu_item(_("Select All"), self._select_all, key_sequence="select-all")
        self._edit_menu.add_separator()

        self._minimize_action = self._window_menu.add_menu_item(_("Minimize"), self._minimize)
        self._zoom_action = self._window_menu.add_menu_item(_("Zoom"), self._zoom)
        self._bring_to_front_action = self._window_menu.add_menu_item(_("Bring to Front"), self._bring_to_front)

        self._file_menu.on_about_to_show = self._file_menu_about_to_show
        self._edit_menu.on_about_to_show = self._edit_menu_about_to_show
        self._window_menu.on_about_to_show = self._window_menu_about_to_show

    def _request_exit(self) -> None:
        if self.app:
            self.app.exit()

    def request_close(self) -> None:
        self.__document_window.request_close()

    def finish_periodic(self) -> None:
        # recognize when we're running as test and finish out periodic operations
        if not self.__document_window.has_event_loop:
            self.periodic()

    def periodic(self) -> None:
        self.__periodic_queue.perform_tasks()
        self.__periodic_set.perform_tasks()
        self.__event_loop.stop()
        self.__event_loop.run_forever()

    @property
    def event_loop(self) -> asyncio.AbstractEventLoop:
        return self.__event_loop

    def attach_widget(self, widget):
        self.__document_window.attach(widget)

    def detach_widget(self):
        self.__document_window.detach()

    def about_to_show(self) -> None:
        if self.__persistent_id:
            geometry = self.ui.get_persistent_string("{}/Geometry".format(self.__persistent_id))
            state = self.ui.get_persistent_string("{}/State".format(self.__persistent_id))
            self.restore(geometry, state)
        self.__shown = True

    def about_to_close(self, geometry: str, state: str) -> None:
        # subclasses can override this method to save geometry and state
        # subclasses can also cancel closing by not calling super() (or close()).
        self.close()

    def refocus_widget(self, widget):
        widget.refocus()

    def __save_bounds(self):
        if self.__shown and self.__persistent_id:
            geometry, state = self.save()
            self.ui.set_persistent_string("{}/Geometry".format(self.__persistent_id), geometry)
            self.ui.set_persistent_string("{}/State".format(self.__persistent_id), state)

    def activation_changed(self, activated: bool) -> None:
        pass

    def size_changed(self, width: int, height: int) -> None:
        self.__save_bounds()

    def position_changed(self, x: int, y: int) -> None:
        self.__save_bounds()

    def drag(self, mime_data, thumbnail, hot_spot_x, hot_spot_y):
        self.__document_window.root_widget.drag(mime_data, thumbnail, hot_spot_x, hot_spot_y)

    @property
    def title(self) -> str:
        return self.__document_window.title

    @title.setter
    def title(self, value: str) -> None:
        self.__document_window.title = value

    def get_file_paths_dialog(self, title: str, directory: str, filter: str, selected_filter: str=None) -> (typing.List[str], str, str):
        return self.__document_window.get_file_paths_dialog(title, directory, filter, selected_filter)

    def get_file_path_dialog(self, title, directory, filter, selected_filter=None):
        return self.__document_window.get_file_path_dialog(title, directory, filter, selected_filter)

    def get_save_file_path(self, title, directory, filter, selected_filter=None):
        return self.__document_window.get_save_file_path(title, directory, filter, selected_filter)

    def create_dock_widget(self, widget, panel_id, title, positions, position):
        return self.__document_window.create_dock_widget(widget, panel_id, title, positions, position)

    def tabify_dock_widgets(self, dock_widget1, dock_widget2):
        return self.__document_window.tabify_dock_widgets(dock_widget1, dock_widget2)

    @property
    def screen_size(self):
        return self.__document_window.screen_size

    @property
    def display_scaling(self):
        return self.__document_window.display_scaling

    def get_font_metrics(self, font, text):
        return self.ui.get_font_metrics(font, text)

    @property
    def focus_widget(self):
        focus_widget = self.__document_window.focus_widget
        if focus_widget:
            return focus_widget
        for dock_widget in self.dock_widgets:
            focus_widget = dock_widget.focus_widget
            if focus_widget:
                return focus_widget
        return None

    @property
    def dock_widgets(self):
        return self.__document_window.dock_widgets

    def show(self) -> None:
        self.__document_window.show()

    def add_menu(self, title: str):
        return self.__document_window.add_menu(title)

    def insert_menu(self, title: str, before_menu):
        return self.__document_window.insert_menu(title, before_menu)

    def create_sub_menu(self):
        return self.ui.create_sub_menu(self.__document_window)

    def create_context_menu(self):
        return self.ui.create_context_menu(self.__document_window)

    def restore(self, geometry: str, state: str) -> None:
        self.__document_window.restore(geometry, state)

    def save(self) -> (str, str):
        return self.__document_window.save()

    # tasks can be added in two ways, queued or added
    # queued tasks are guaranteed to be executed in the order queued.
    # added tasks are only executed if not replaced before execution.
    # added tasks do not guarantee execution order or execution at all.

    def add_task(self, key, task):
        assert task
        self.__periodic_set.add_task(key + str(id(self)), task)

    def clear_task(self, key):
        self.__periodic_set.clear_task(key + str(id(self)))

    def queue_task(self, task):
        assert task
        self.__periodic_queue.put(task)

    def handle_quit(self):
        self.app.exit()

    def _dispatch_any_to_focus_widget(self, method: str, *args, **kwargs) -> bool:
        focus_widget = self.focus_widget
        if focus_widget and focus_widget._dispatch_any(method, *args, **kwargs):
                return True
        if hasattr(self, method) and getattr(self, method)(*args, **kwargs):
                return True
        return False

    def _will_focus_widget_dispatch(self, method: str) -> bool:
        focus_widget = self.focus_widget
        if focus_widget and focus_widget._will_dispatch(method):
            return True
        if hasattr(self, method):
            return True
        return False

    # standarad menu items

    def _file_menu_about_to_show(self):
        self._close_action.enabled = True
        self._page_setup_action.enabled = self._will_focus_widget_dispatch("handle_page_setup")
        self._print_action.enabled = self._will_focus_widget_dispatch("handle_print")
        self._quit_action.enabled = True

    def _edit_menu_about_to_show(self):
        self._undo_action.enabled = self._will_focus_widget_dispatch("handle_undo")
        self._redo_action.enabled = self._will_focus_widget_dispatch("handle_redo")
        self._cut_action.enabled = self._will_focus_widget_dispatch("handle_cut")
        self._copy_action.enabled = self._will_focus_widget_dispatch("handle_copy")
        self._paste_action.enabled = self._will_focus_widget_dispatch("handle_paste")
        self._delete_action.enabled = self._will_focus_widget_dispatch("handle_delete")
        self._select_all_action.enabled = self._will_focus_widget_dispatch("handle_select_all")

    def _window_menu_about_to_show(self):
        self._minimize_action.enabled = self._will_focus_widget_dispatch("handle_minimize")
        self._zoom_action.enabled = self._will_focus_widget_dispatch("handle_zoom")
        self._bring_to_front_action.enabled = self._will_focus_widget_dispatch("handle_bring_to_front")

    def _page_setup(self):
        self._dispatch_any_to_focus_widget("handle_page_setup")

    def _print(self):
        self._dispatch_any_to_focus_widget("handle_print")

    def _cut(self):
        self._dispatch_any_to_focus_widget("handle_cut")

    def _copy(self):
        self._dispatch_any_to_focus_widget("handle_copy")

    def _paste(self):
        self._dispatch_any_to_focus_widget("handle_paste")

    def _delete(self):
        self._dispatch_any_to_focus_widget("handle_delete")

    def _select_all(self):
        self._dispatch_any_to_focus_widget("handle_select_all")

    def _undo(self):
        self._dispatch_any_to_focus_widget("handle_undo")

    def _redo(self):
        self._dispatch_any_to_focus_widget("handle_redo")

    def _minimize(self):
        self._dispatch_any_to_focus_widget("handle_minimize")

    def _zoom(self):
        self._dispatch_any_to_focus_widget("handle_zoom")

    def _bring_to_front(self):
        self._dispatch_any_to_focus_widget("bring_to_front")
