"""Unit tests for dkr's parsing/formatting helpers (no Docker daemon needed).

Run:  uv run --with pytest --with textual --with rich pytest
"""
import importlib.machinery
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_loader = importlib.machinery.SourceFileLoader("dkr", str(ROOT / "dkr"))
_spec = importlib.util.spec_from_loader("dkr", _loader)
dkr = importlib.util.module_from_spec(_spec)
sys.modules["dkr"] = dkr
_loader.exec_module(dkr)


# Real `docker ps --format '{{json .}}'` rows (trimmed).
DOCKER_ROW = {
    "ID": "c8df9778675e", "Image": "ghcr.io/example/app:3.3.0", "Names": "notes-app-1",
    "State": "running", "Status": "Up 5 hours", "Ports": "3443/tcp, 127.0.0.1:3040->3000/tcp",
    "Command": '"tini -s -- node --n…"', "CreatedAt": "2026-11-10 15:54:24 +0200 EET",
    "Labels": "com.docker.compose.depends_on=postgres:service_healthy:false,redis:service_healthy:false,"
              "com.docker.compose.project.working_dir=/home/me/notes,com.docker.compose.project=notes,"
              "com.docker.compose.service=app,org.opencontainers.image.description=Fast, realtime, collaborative",
}
PODMAN_ROW = {
    "Id": "abcdef1234567890", "Image": "docker.io/library/redis:7", "Names": ["cache"], "State": "running",
    "Status": "Up 3 minutes", "Labels": {"com.docker.compose.project": "app", "com.docker.compose.service": "cache"},
    "Ports": [{"host_ip": "", "container_port": 6379, "host_port": 6379, "range": 1, "protocol": "tcp"}],
    "Command": ["redis-server"],
}


def test_parse_labels_values_with_commas():
    labels = dkr.parse_labels(DOCKER_ROW["Labels"])
    assert labels["com.docker.compose.project"] == "notes"
    assert labels["com.docker.compose.service"] == "app"
    assert labels["com.docker.compose.depends_on"] == "postgres:service_healthy:false,redis:service_healthy:false"
    assert labels["org.opencontainers.image.description"] == "Fast, realtime, collaborative"


def test_parse_container_docker_and_podman():
    c = dkr.parse_container(DOCKER_ROW)
    assert (c.id, c.name, c.project, c.service) == ("c8df9778675e", "notes-app-1", "notes", "app")
    assert c.running
    p = dkr.parse_container(PODMAN_ROW)
    assert (p.id, p.name, p.project, p.command) == ("abcdef123456", "cache", "app", "redis-server")
    assert dkr.fmt_ports(p.ports) == "6379"


def test_fmt_ports():
    assert dkr.fmt_ports("0.0.0.0:8080->80/tcp, [::]:8080->80/tcp") == "8080→80"
    assert dkr.fmt_ports("0.0.0.0:5432->5432/tcp, :::5432->5432/tcp") == "5432"
    assert dkr.fmt_ports("3443/tcp, 127.0.0.1:3040->3000/tcp") == "lo:3040→3000"
    assert dkr.fmt_ports("3443/tcp", published_only=False) == "(3443)"
    assert dkr.fmt_ports("0.0.0.0:53->53/udp") == "53/udp"
    assert dkr.fmt_ports("") == ""


def test_fmt_status():
    assert dkr.fmt_status("Up 7 days (healthy)", "running").plain == "7d ✓"
    assert dkr.fmt_status("Up About an hour", "running").plain == "1h"
    assert dkr.fmt_status("Up Less than a second", "running").plain == "<1s"
    assert dkr.fmt_status("Up 3 minutes (unhealthy)", "running").plain == "3m ✗ unhealthy"
    assert dkr.fmt_status("Up 4 seconds (health: starting)", "running").plain == "4s … starting"
    assert dkr.fmt_status("Exited (1) 5 minutes ago", "exited").plain == "exit 1 · 5m ago"
    assert dkr.fmt_status("Exited (127) 2 weeks ago", "exited").plain == "exit 127 · 2w ago"
    assert dkr.fmt_status("Created", "created").plain == "created"
    assert dkr.fmt_status("Up 2 hours (Paused)", "paused").plain == "paused"
    assert dkr.fmt_status("Restarting (1) 3 seconds ago", "restarting").plain == "restarting"


def test_short_image():
    assert dkr.short_image("ghcr.io/example/app:3.3.0") == "app:3.3.0"
    assert dkr.short_image("postgres:16") == "postgres:16"
    assert dkr.short_image("docker.io/library/redis:7") == "redis:7"
    assert dkr.short_image("nginx@sha256:abc") == "nginx"
    assert dkr.short_image("sha256:e923a22ac074261dd73970") == "e923a22ac074"


def test_tilde_shortens_long_paths():
    home = str(Path.home())
    assert dkr.tilde(home + "/code/app") == "~/code/app"
    short = dkr.tilde("/private/tmp/some/very/long/scratch/dir/project", 20)
    assert len(short) <= 20 and short.endswith("project")


def _c(name, project=None, service=None, state="running", status="Up 1 hour"):
    labels = {}
    if project:
        labels = {dkr.L_PROJECT: project, dkr.L_SERVICE: service or name}
    return dkr.Container(id=name[:12], name=name, image="img", state=state, status=status, ports="", labels=labels)


def test_build_rows_grouping_and_order():
    containers = [_c("solo"), _c("b-web-1", "beta", "web"), _c("a-db-1", "alpha", "db", "exited", "Exited (1) 1m ago"),
                  _c("h-api-1", "here", "api")]
    here = dkr.Project("here", "/x", ["/x/compose.yaml"], services={"api": {}, "worker": {}}, is_cwd=True)
    projects = dkr.collect_projects(containers, here)
    rows = dkr.build_rows(containers, projects)
    groups = [r.label for r in rows if r.kind == "group"]
    assert groups == ["here", "alpha", "beta", dkr.STANDALONE]  # cwd project first, standalone last
    here_rows = [(r.kind, r.label) for r in rows[:3]]
    assert here_rows == [("group", "here"), ("container", "api"), ("service", "worker")]
    assert rows[0].running == 1 and rows[0].total == 2

    running_only = dkr.build_rows(containers, projects, show_stopped=False)
    assert "alpha" not in [r.label for r in running_only if r.kind == "group"]
    assert not any(r.kind == "service" for r in running_only)

    filtered = dkr.build_rows(containers, projects, filt="web")
    # empty groups are hidden while filtering
    assert [(r.kind, r.label) for r in filtered] == [("group", "beta"), ("container", "web")]


def test_find_compose_file_walks_up(tmp_path):
    (tmp_path / "compose.yaml").write_text("services: {}\n")
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert dkr.find_compose_file(deep) == tmp_path / "compose.yaml"


def test_compose_base_uses_project_files(tmp_path):
    f = tmp_path / "docker-compose.yml"
    f.write_text("services: {}\n")
    eng = dkr.Engine("docker", ["docker", "compose"])
    cmd, cwd = dkr.compose_base(eng, dkr.Project("p", str(tmp_path), [str(f)]))
    assert cmd == ["docker", "compose", "-p", "p", "-f", str(f), "--project-directory", str(tmp_path)]
    assert cwd == str(tmp_path)
    cmd, cwd = dkr.compose_base(eng, dkr.Project("gone", "/nope", ["/nope/compose.yaml"]))
    assert cmd == ["docker", "compose", "-p", "gone"] and cwd is None


def test_sizes():
    assert dkr.parse_size("6.92GB") == 6_920_000_000
    assert dkr.parse_size("32.77kB (0%)") == 32_770
    assert dkr.parse_size("5.586MiB") == int(5.586 * 1024**2)
    assert dkr.parse_size("N/A") == 0
    assert dkr.fmt_size(6_920_000_000) == "6.9 GB"
    assert dkr.fmt_size(544_000_000) == "544 MB"
    assert dkr.fmt_size(0) == "0 B"


def _res(kind, name, used_by=(), project=None, **extra):
    labels = {dkr.L_PROJECT: project} if project else {}
    return dkr.Resource(kind, name, name, used_by=list(used_by), labels=labels, extra=extra)


def test_scope_resources():
    containers = [_c("app-web-1", "app", "web"), _c("other-db-1", "other", "db")]
    vols = [_res("volume", "app_data", project="app"), _res("volume", "other_data", ["other-db-1"]),
            _res("volume", "shared", ["app-web-1"])]
    assert [v.name for v in dkr.scope_resources(vols, "app", containers)] == ["app_data", "shared"]
    assert len(dkr.scope_resources(vols, None, containers)) == 3
    imgs = [_res("image", "app-web:latest", repo="app-web"), _res("image", "nginx:1", repo="nginx")]
    assert [i.name for i in dkr.scope_resources(imgs, "app", containers)] == ["app-web:latest"]


def test_prune_plan_counts_only_unused():
    containers = [_c("a"), _c("b", state="exited", status="Exited (1) 1m ago")]
    res = dkr.Resources(
        images=[_res("image", "used", ["a"]), _res("image", "<untagged>", dangling=True),
                _res("image", "old:1")],
        volumes=[_res("volume", "data", count=1), _res("volume", "orphan")],
        networks=[_res("network", "bridge", builtin=True), _res("network", "lonely")],
        build_cache=1000, loaded=True)
    res.images[1].size, res.images[2].size, res.volumes[1].size = 10, 20, 30
    plan = {p.key: p for p in dkr.build_prune_plan(dkr.Engine("docker", ["docker", "compose"]), containers, res)}
    assert plan["containers"].names == ["b"]
    assert (plan["dangling"].count, plan["dangling"].size) == (1, 10)
    assert (plan["images"].count, plan["images"].size) == (2, 30)
    assert plan["networks"].names == ["lonely"]
    assert plan["volumes"].names == ["orphan"] and plan["volumes"].warn and not plan["volumes"].on
    assert plan["images"].on is False and plan["cache"].size == 1000
    assert plan["volumes"].cmd == ["docker", "volume", "prune", "-a", "-f"]


def test_build_rows_only_one_stack():
    containers = [_c("a-web-1", "a", "web"), _c("b-web-1", "b", "web"), _c("solo")]
    projects = dkr.collect_projects(containers, None)
    rows = dkr.build_rows(containers, projects, only="a")
    assert [r.label for r in rows] == ["a", "web"]


def test_scoped_resource_hides_other_stacks_but_stays_in_use():
    containers = [_c("app-db-1", "app", "db"), _c("other-db-1", "other", "db")]
    img = _res("image", "postgres:16", ["app-db-1", "other-db-1"], count=2)
    (scoped,) = dkr.scope_resources([img], "app", containers)
    assert scoped.used_by == ["app-db-1"] and scoped.extra["elsewhere"] == 1 and scoped.in_use
    assert img.used_by == ["app-db-1", "other-db-1"]  # original untouched
