"""Dependency and container hygiene: workflows pin actions to SHAs, PR jobs are read-only, compose hardens the app container."""
from __future__ import annotations
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.yml"))
SHA_USE = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")


def steps(doc):
    for job in doc["jobs"].values():
        yield from job.get("steps", [])


def test_every_action_is_pinned_to_a_sha_with_a_version_comment():
    assert WORKFLOWS
    for wf in WORKFLOWS:
        text = wf.read_text()
        for st in steps(yaml.safe_load(text)):
            use = st.get("uses")
            if use and not use.startswith("./"):
                assert SHA_USE.match(use), f"{wf.name}: {use} is not pinned to a full commit SHA"
        for line in text.splitlines():
            if re.search(r"uses: .+@[0-9a-f]{40}", line):
                assert re.search(r"# v\d+(\.\d+)*\s*$", line), f"{wf.name}: no version comment on: {line.strip()}"


def test_workflow_permissions_are_scoped_per_job():
    doc = yaml.safe_load((ROOT / ".github/workflows/build.yml").read_text())
    assert doc["permissions"] == {"contents": "read"}
    jobs = doc["jobs"]
    for name in ("test", "image-check"):
        assert jobs[name]["permissions"] == {"contents": "read"}
    assert jobs["release"]["permissions"] == {"contents": "write", "pull-requests": "write"}
    assert jobs["image"]["permissions"] == {"contents": "read", "packages": "write"}
    assert jobs["image-check"]["if"] == "github.event_name == 'pull_request'"
    assert "pull_request" not in jobs["image"]["if"]


def test_pr_check_scans_the_loaded_image_with_trivy():
    jobs = yaml.safe_load((ROOT / ".github/workflows/build.yml").read_text())["jobs"]
    st = jobs["image-check"]["steps"]
    build = next(s for s in st if s["uses"].startswith("docker/build-push-action@"))
    assert build["with"]["load"] is True and build["with"]["push"] is False
    trivy = next(s for s in st if s["uses"].startswith("aquasecurity/trivy-action@"))
    assert trivy["with"]["image-ref"] == build["with"]["tags"] and trivy["with"]["exit-code"] == "1"
    assert st.index(trivy) > st.index(build)


def test_compose_app_container_is_hardened():
    svc = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]["ledger"]
    assert svc["read_only"] is True
    assert svc["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in svc["security_opt"]
    assert any(t.split(":")[0] == "/tmp" for t in svc["tmpfs"])


def test_dockerfile_forwarded_allow_ips_is_env_configurable_with_todays_default():
    df = (ROOT / "Dockerfile").read_text()
    assert "FORWARDED_ALLOW_IPS='*'" in df
    assert '--forwarded-allow-ips \\"${FORWARDED_ALLOW_IPS}\\"' in df


def test_dependabot_does_not_group_minor_bumps_of_zero_x_packages():
    groups = next(u for u in yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())["updates"] if u["package-ecosystem"] == "pip")["groups"]
    zero = {re.sub(r"\[.*\]", "", p) for p in re.findall(r"^([A-Za-z0-9_.\[\]-]+)==0\.", (ROOT / "requirements.txt").read_text(), re.M)}
    assert zero
    for name, grp in groups.items():
        if "minor" in grp["update-types"]:
            pats = set(grp["patterns"])
            covered = zero - set(grp.get("exclude-patterns", [])) if "*" in pats else zero & pats
            assert not covered, f"group {name} bundles minor bumps of 0.x packages {covered}"
    assert zero <= set(groups["python-0x-patch"]["patterns"])
