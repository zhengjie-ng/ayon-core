from qtpy import QtWidgets

from .report_items import (
    PublishReport
)
from .widgets import (
    PublishReportViewerWidget
)

from .window import (
    PublishReportViewerWindow
)


__all__ = (
    "PublishReport",

    "PublishReportViewerWidget",

    "PublishReportViewerWindow",

    "main",
)


def main():
    app = QtWidgets.QApplication([])
    window = PublishReportViewerWindow()
    window.show()
    return app.exec_()
