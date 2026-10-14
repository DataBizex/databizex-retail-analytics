import os

import pytest


@pytest.fixture
def window(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    import app.ui.main_window as main_window

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    win = main_window.MainWindow()
    yield win, main_window, app
    win.close()
    app.processEvents()


def test_kpi_tab_has_predefined_and_custom_views_with_room_for_the_table(window):
    win, _main_window, app = window
    titles = [win.kpi_views.tabText(i) for i in range(win.kpi_views.count())]
    assert titles == ["Predefined queries", "Custom SQL"]
    assert win.kpi_views.widget(0).isAncestorOf(win.result_table)
    assert win.kpi_views.widget(1).isAncestorOf(win.custom_sql)
    assert win.kpi_views.widget(1).isAncestorOf(win.custom_table)

    assert win.load_sample()
    assert win.run_kpis()
    win.resize(1366, 768)
    win.show()
    win.tabs.setCurrentIndex(2)  # SQL & KPIs
    app.processEvents()
    assert win.size().toTuple() == (1366, 768)
    rows = win.result_table.viewport().height() / win.result_table.verticalHeader().defaultSectionSize()
    assert rows >= 6, f"only {rows:.1f} rows fit in the predefined result table"


def test_charts_scroll_with_a_minimum_height(window):
    win, main_window, _app = window
    canvases = win.chart_scroll.widget().findChildren(main_window.ChartCanvas)
    assert set(canvases) == {win.country_chart, win.month_chart, win.pareto_chart}
    assert all(c.minimumHeight() >= 320 for c in canvases)
    assert win.chart_scroll.widgetResizable()


def test_window_has_a_minimum_size(window):
    win, _main_window, _app = window
    assert (win.minimumWidth(), win.minimumHeight()) == (1100, 700)


def test_pareto_names_only_the_top_ten_products(window):
    win, _main_window, _app = window
    assert win.load_sample()
    assert win.run_kpis()
    pareto = win.kpi_results["pareto"]
    ax = win.pareto_chart.figure.axes[0]
    labels = [t.get_text() for t in ax.get_xticklabels()]
    assert len(labels) == len(pareto) == 26  # every bar keeps its position
    assert labels[0] == "Two-Seat Sofa" and all(labels[:10]) and not any(labels[10:])
    assert ax.get_xticklabels()[0].get_rotation() == 35
    assert len(ax.patches) == 26  # all products are still drawn
