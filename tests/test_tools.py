import hashlib
import io
import os
import stat
import sys
import tarfile
import threading
import zipfile
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler

import pytest

from ytdlp_gui import engine, tools
from ytdlp_gui.config import Settings


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "srv"
    root.mkdir()
    handler = partial(SimpleHTTPRequestHandler, directory=str(root))
    handler.log_message = lambda *a: None
    class QuietServer(HTTPServer):
        def handle_error(self, request, client_address):
            pass  # clients hanging up early (cancel test) is expected

    httpd = QuietServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    old = {k: os.environ.pop(k, None) for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy")}
    yield root, f"http://127.0.0.1:{httpd.server_port}/"
    httpd.shutdown()
    for k, v in old.items():
        if v is not None:
            os.environ[k] = v


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _spec(base, name, binaries, checksum="sums.txt"):
    return tools.ToolSpec("x", "x", base + name, base + checksum, name, binaries, 1)


def test_install_zip(server, isolated_tools):
    root, base = server
    z = root / "deno.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("deno" + tools.EXE, b"#!/bin/sh\necho deno\n")
        zf.writestr("README.md", b"x")
    (root / "sums.txt").write_text(f"{_sha(z)}  deno.zip\n")
    progress = []
    path = tools.install("deno", progress.append, spec=_spec(base, "deno.zip", ("deno",)))
    assert path == str(isolated_tools / ("deno" + tools.EXE))
    assert progress[-1] == 100.0
    if sys.platform != "win32":
        assert os.stat(path).st_mode & stat.S_IXUSR
    assert tools.find_js_runtime() == ("deno", path)
    assert "deno" in tools.installed_versions()
    assert not (isolated_tools / "README.md").exists()


def test_install_tarxz_only_wanted_and_flattened(server, isolated_tools):
    root, base = server
    tx = root / "ff.tar.xz"
    with tarfile.open(tx, "w:xz") as t:
        for name in ("ffmpeg-master/bin/ffmpeg", "ffmpeg-master/bin/ffprobe", "ffmpeg-master/bin/ffplay",
                     "../../evil/ffmpeg-escape"):
            data = b"bin"
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
    (root / "sums.txt").write_text(f"{'0' * 64}  other.zip\n{_sha(tx)}  ff.tar.xz\n")
    tools.install("ffmpeg", spec=_spec(base, "ff.tar.xz", ("ffmpeg", "ffprobe")))
    names = sorted(p.name for p in isolated_tools.iterdir() if not p.name.startswith("."))
    assert names == sorted(["ffmpeg" + tools.EXE, "ffprobe" + tools.EXE, "versions.json"])
    assert not (isolated_tools.parent / "evil").exists()


def test_checksum_mismatch_rejected(server, isolated_tools):
    root, base = server
    z = root / "deno.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("deno" + tools.EXE, b"tampered")
    (root / "sums.txt").write_text(f"{'a' * 64}  deno.zip\n")
    with pytest.raises(tools.ToolError, match="Checksum mismatch"):
        tools.install("deno", spec=_spec(base, "deno.zip", ("deno",)))
    assert tools.managed_path("deno") is None


def test_missing_binary_in_archive(server):
    root, base = server
    z = root / "x.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("other", b"x")
    (root / "sums.txt").write_text(f"{_sha(z)}  x.zip\n")
    with pytest.raises(tools.ToolError, match="did not contain"):
        tools.install("deno", spec=_spec(base, "x.zip", ("deno",)))


def test_cancel(server):
    root, base = server
    z = root / "deno.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("deno" + tools.EXE, os.urandom(300_000))
    (root / "sums.txt").write_text(f"{_sha(z)}  deno.zip\n")
    ev = threading.Event()
    ev.set()
    with pytest.raises(tools.Cancelled):
        tools.install("deno", cancel=ev, spec=_spec(base, "deno.zip", ("deno",)))


def test_js_runtime_fallback_to_path(monkeypatch):
    monkeypatch.setattr(tools.shutil, "which", lambda exe: "/usr/bin/node" if exe == "node" else None)
    assert tools.find_js_runtime() == ("node", "/usr/bin/node")
    monkeypatch.setattr(tools.shutil, "which", lambda exe: None)
    assert tools.find_js_runtime() is None


def test_build_opts_uses_js_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "find_js_runtime", lambda: ("deno", "/opt/deno"))
    o = engine.build_opts(Settings(output_dir=str(tmp_path)), engine.JobSpec("u"))
    assert o["js_runtimes"] == {"deno": {"path": "/opt/deno"}}
    assert engine.probe_opts(Settings(), None)["js_runtimes"] == {"deno": {"path": "/opt/deno"}}


def test_specs_per_platform():
    for plat in ("win-x64", "linux-x64", "linux-arm64"):
        s = tools.specs(plat)
        assert s["deno"].url.endswith(s["deno"].archive_name)
        assert s["ffmpeg"].archive_name in s["ffmpeg"].url
    assert tools.specs("win-x64")["ffmpeg"].archive_name.endswith(".zip")
    assert tools.specs("linux-x64")["ffmpeg"].archive_name.endswith(".tar.xz")
