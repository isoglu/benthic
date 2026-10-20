def ui_client(app):
    """Testclient wie die Oberfläche der App: mit eigenem Origin (siehe den Schutz in app.py)."""
    client = app.test_client()
    client.environ_base["HTTP_ORIGIN"] = "http://localhost"
    return client
