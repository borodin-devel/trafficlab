"""Run owner for Validation Study tooling."""

from __future__ import annotations

import hashlib
import os
import platform
import stat
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from scripts.validation_study.common import (
    STUDY_ID_PATTERN,
    SUBPROCESS_TIMEOUTS,
    TARGET_REFERENCE,
    FrozenJsonValue,
    container_id_value,
    freeze_object,
    git_commit_value,
    image_id_value,
    path_entry_exists,
    publish_support_json,
    repository_relative_path,
    require,
    strict_string,
    validate_endpoint_url,
    validate_study_id,
)
from scripts.validation_study.prerequisites.codec import (
    build_expected_capability_argv,
    parse_prerequisite_results,
    render_prerequisite_results,
)
from scripts.validation_study.prerequisites.commands import (
    best_effort_preserve_capability_canary,
    capability_header_values,
    capability_write_out,
    command_detail,
    completed_output,
    container_listing,
    docker_matrix_argv,
    internet_smoke_argv,
    parse_junit_counts,
    private_bytes,
    remove_owned_capability_if_present,
    remove_owned_prerequisite_capture_image,
    require_clean_prerequisite_worktree,
    retain_failed_capability_output,
    run_prerequisite_test,
    stdout_text,
    target_image_record,
    timeout_bytes,
    timestamp_now,
)
from scripts.validation_study.records import PrerequisiteResults
from scripts.validation_study.rotation.run import (
    begin_phase_attempt,
    commit_prerequisite_rotation,
    recover_incomplete_prerequisite_rotations,
)
from scripts.validation_study.workloads import build_base_config, render_checked_base_config_content, workload_specs
from trafficlab import __version__
from trafficlab.capture.docker.image import (
    cold_capture_build_argv,
    load_capture_image_lock,
    validate_capture_dockerfile,
)
from trafficlab.common.config import ExperimentConfig
from trafficlab.common.errors import TrafficlabError
from trafficlab.comparison.codec import (
    sha256_bytes,
)

if TYPE_CHECKING:
    from scripts.validation_study.common import JsonObject
    from scripts.validation_study.records import CommandRunner


def _cleanup_failed_capability(
    *, repository_root: Path, study_id: str, capability_name: str, capability_cid: Path, runner: CommandRunner
) -> str:
    try:
        container_id = capability_cid.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError) as error:
        return f"cleanup could not read the exclusive CID; container name {capability_name} may remain: {error}"
    try:
        container_id = container_id_value(container_id, name="capability CID")
        capability_cid.chmod(384)
        removed = remove_owned_capability_if_present(
            repository_root=repository_root,
            study_id=study_id,
            capability_name=capability_name,
            container_id=container_id,
            runner=runner,
        )
    except (OSError, TypeError, ValueError, subprocess.SubprocessError) as error:
        return f"cleanup incomplete: {error}"
    if removed:
        return f"owned capability container {container_id} was removed and its ID is absent"
    return f"capability container {container_id} is absent"


def _prepare_capability(
    *,
    repository_root: Path,
    study_id: str,
    url: str,
    evidence_directory: Path,
    mount_directory: Path,
    runner: CommandRunner,
    utc_now: Callable[[], datetime],
) -> JsonObject:
    capability_name = f"trafficlab-validation-study-capability-{study_id}"
    capability_cid = evidence_directory / "capability.cid"
    canary = mount_directory / ".capability.headers"
    require(not path_entry_exists(capability_cid), "capability CID path must be absent before launch")
    require(not path_entry_exists(canary), "capability canary path must be absent before launch")
    require(
        not container_listing(repository_root, f"name=^/{capability_name}$", runner=runner),
        f"capability container name already exists: {capability_name}",
    )
    descriptor = os.open(canary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 438)
    os.close(descriptor)
    canary.chmod(438)
    inode = canary.lstat().st_ino
    checked_argv = build_expected_capability_argv(study_id, url)
    live_argv = list(checked_argv)
    live_argv[8] = str(capability_cid)
    live_argv[12] = f"type=bind,src={mount_directory},dst=/trafficlab-study"
    require("--user" not in live_argv, "capability must use the image default user")
    started = timestamp_now(utc_now)
    try:
        completed = runner(
            tuple(live_argv),
            cwd=repository_root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["capability"],
        )
    except subprocess.TimeoutExpired as error:
        retained = retain_failed_capability_output(
            evidence_directory, stdout=timeout_bytes(error.output), stderr=timeout_bytes(error.stderr)
        )
        cleanup = _cleanup_failed_capability(
            repository_root=repository_root,
            study_id=study_id,
            capability_name=capability_name,
            capability_cid=capability_cid,
            runner=runner,
        )
        raise ValueError(f"capability command timed out after 45 seconds; {cleanup}; {retained}") from error
    except OSError as error:
        cleanup = _cleanup_failed_capability(
            repository_root=repository_root,
            study_id=study_id,
            capability_name=capability_name,
            capability_cid=capability_cid,
            runner=runner,
        )
        raise ValueError(f"capability command could not start: {error}; {cleanup}") from error
    completed_time = timestamp_now(utc_now)
    stdout, stderr = completed_output(completed, operation="capability")
    if completed.returncode != 0:
        retained = retain_failed_capability_output(evidence_directory, stdout=stdout, stderr=stderr)
        cleanup = _cleanup_failed_capability(
            repository_root=repository_root,
            study_id=study_id,
            capability_name=capability_name,
            capability_cid=capability_cid,
            runner=runner,
        )
        raise ValueError(
            f"capability command failed with status {completed.returncode}: {command_detail(completed, operation='capability')}; {cleanup}; {retained}"
        )
    private_bytes(evidence_directory / "capability.stdout", stdout)
    private_bytes(evidence_directory / "capability.stderr", stderr)
    try:
        container_id = capability_cid.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError(f"could not read capability CID: {error}") from error
    container_id = container_id_value(container_id, name="capability CID")
    capability_cid.chmod(384)
    remove_owned_capability_if_present(
        repository_root=repository_root,
        study_id=study_id,
        capability_name=capability_name,
        container_id=container_id,
        runner=runner,
    )
    metadata = canary.lstat()
    require(
        stat.S_ISREG(metadata.st_mode)
        and (not stat.S_ISLNK(metadata.st_mode))
        and (metadata.st_ino == inode)
        and (stat.S_IMODE(metadata.st_mode) == 438),
        "capability canary must preserve its exclusive regular 0666 inode",
    )
    header_bytes = canary.read_bytes()
    require(bool(header_bytes), "capability canary must be nonempty")
    after_read = canary.lstat()
    require(after_read.st_ino == inode, "capability canary inode changed while reading")
    redirect_count, object_size, content_range, header_final_url = capability_header_values(
        header_bytes, initial_url=url
    )
    status, downloaded, write_final_url, write_redirects = capability_write_out(stdout)
    require(
        (write_final_url, write_redirects) == (header_final_url, redirect_count),
        "capability write-out URL and redirect count must equal header evidence",
    )
    archive = evidence_directory / "capability.headers"
    private_bytes(archive, header_bytes)
    canary.unlink()
    return {
        "argv": list(checked_argv),
        "started_utc": started,
        "completed_utc": completed_time,
        "exit_status": completed.returncode,
        "status": status,
        "content_length": 1,
        "object_size_bytes": object_size,
        "redirect_count": redirect_count,
        "body_bytes_downloaded": downloaded,
        "content_range": content_range,
        "final_url": header_final_url,
        "mount_source": f"examples/validation_study/.study-work/mount/{study_id}",
        "canary_archive_path": f"examples/validation_study/.study-work/evidence/{study_id}/00-prerequisites/capability.headers",
        "canary_sha256": hashlib.sha256(header_bytes).hexdigest(),
        "container_id": container_id,
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest(),
        "used_image_default_user": True,
        "mount_directory_mode": stat.S_IMODE(mount_directory.lstat().st_mode),
        "canary_file_mode": stat.S_IMODE(metadata.st_mode),
        "canary_archive_mode": stat.S_IMODE(archive.lstat().st_mode),
        "container_cleanup_verified": True,
    }


def validate_prerequisite_evidence(repository_root: Path, prerequisites: PrerequisiteResults) -> None:
    root = repository_root.resolve()
    evidence_directory = (
        root
        / "examples"
        / "validation_study"
        / ".study-work"
        / "evidence"
        / prerequisites.study_id
        / "00-prerequisites"
    )
    expected_names = {
        "capability.headers",
        "capability.stdout",
        "capability.stderr",
        "capability.cid",
        "capture.iid",
        "docker.stdout",
        "docker.stderr",
        "docker.xml",
        "internet.stdout",
        "internet.stderr",
        "internet.xml",
    }
    try:
        directory_mode = evidence_directory.lstat().st_mode
        require(
            stat.S_ISDIR(directory_mode)
            and (not stat.S_ISLNK(directory_mode))
            and (evidence_directory.resolve() == evidence_directory),
            "prerequisite evidence directory must be the exact non-symlink study directory",
        )
        entries = tuple(evidence_directory.iterdir())
        require({entry.name for entry in entries} == expected_names, "prerequisite evidence must use exact file names")
        require(
            all(
                stat.S_ISREG(entry.lstat().st_mode)
                and (not stat.S_ISLNK(entry.lstat().st_mode))
                and (stat.S_IMODE(entry.lstat().st_mode) == 384)
                for entry in entries
            ),
            "every retained prerequisite evidence file must be a regular non-symlink at mode 0600",
        )
        contents = {entry.name: entry.read_bytes() for entry in entries}
        dockerfile_path = root / "docker" / "capture" / "Dockerfile"
        capture_script_path = root / "docker" / "capture" / "capture.sh"
        source_modes = (dockerfile_path.lstat().st_mode, capture_script_path.lstat().st_mode)
        require(
            all(stat.S_ISREG(mode) and (not stat.S_ISLNK(mode)) for mode in source_modes)
            and dockerfile_path.resolve() == dockerfile_path
            and (capture_script_path.resolve() == capture_script_path),
            "capture source files must be exact regular non-symlinks",
        )
        dockerfile = dockerfile_path.read_bytes()
        capture_script = capture_script_path.read_bytes()
    except OSError as error:
        raise ValueError(f"could not read retained prerequisite evidence: {error}") from error
    images = prerequisites.images
    require(
        sha256_bytes(dockerfile) == images["capture_dockerfile_sha256"]
        and sha256_bytes(capture_script) == images["capture_script_sha256"],
        "capture source files must match prerequisite source hashes",
    )
    capability = prerequisites.capability
    archive_record = repository_relative_path(
        capability["canary_archive_path"], repository_root=root, name="capability evidence path"
    )
    require(
        root / Path(*archive_record.split("/")) == evidence_directory / "capability.headers",
        "capability archive record must resolve to its exact retained evidence file",
    )
    require(
        sha256_bytes(contents["capability.headers"]) == capability["canary_sha256"]
        and sha256_bytes(contents["capability.stdout"]) == capability["stdout_sha256"]
        and (sha256_bytes(contents["capability.stderr"]) == capability["stderr_sha256"]),
        "retained capability hashes must match prerequisite evidence",
    )
    redirect_count, object_size, content_range, final_url = capability_header_values(
        contents["capability.headers"], initial_url=prerequisites.url
    )
    status, downloaded, write_final_url, write_redirects = capability_write_out(contents["capability.stdout"])
    require(
        (redirect_count, object_size, content_range, final_url)
        == (
            capability["redirect_count"],
            capability["object_size_bytes"],
            capability["content_range"],
            capability["final_url"],
        )
        and (status, downloaded, write_final_url, write_redirects)
        == (capability["status"], capability["body_bytes_downloaded"], final_url, redirect_count),
        "retained capability headers and write-out must match prerequisite facts",
    )
    try:
        retained_cid = container_id_value(
            contents["capability.cid"].decode("ascii").strip(), name="retained capability container ID"
        )
        retained_iid = image_id_value(contents["capture.iid"].decode("ascii").strip(), name="retained capture image ID")
    except UnicodeDecodeError as error:
        raise ValueError("retained capability CID and capture IID must be ASCII") from error
    require(
        retained_cid == capability["container_id"] and retained_iid == images["capture_image_id"],
        "retained capability CID and capture IID must match prerequisite identities",
    )
    for command, prefix, expected_kind in zip(
        prerequisites.commands, ("docker", "internet"), ("docker_matrix", "internet_smoke"), strict=True
    ):
        require(command["kind"] == expected_kind, "retained prerequisite commands must retain exact kind order")
        argv = cast(tuple[FrozenJsonValue, ...], command["argv"])
        junit_record = repository_relative_path(argv[-1], repository_root=root, name=f"{prefix} JUnit path")
        require(
            root / Path(*junit_record.split("/")) == evidence_directory / f"{prefix}.xml",
            f"{prefix} JUnit record must resolve to its exact retained evidence file",
        )
        stdout = contents[f"{prefix}.stdout"]
        stderr = contents[f"{prefix}.stderr"]
        junit = contents[f"{prefix}.xml"]
        require(
            sha256_bytes(stdout) == command["stdout_sha256"]
            and sha256_bytes(stderr) == command["stderr_sha256"]
            and (sha256_bytes(junit) == command["junit_sha256"]),
            f"retained {prefix} output and JUnit hashes must match prerequisite evidence",
        )
        require(parse_junit_counts(junit) == command["tests"], f"retained {prefix} JUnit counts must match evidence")


def publish_prerequisites(
    path: Path, value: PrerequisiteResults, *, repository_root: Path, replace_existing: bool = False
) -> None:
    content = render_prerequisite_results(value)

    def validate(persisted: bytes) -> None:
        parsed = parse_prerequisite_results(persisted, repository_root=repository_root)
        if render_prerequisite_results(parsed) != content:
            raise ValueError("persisted prerequisite JSON is not canonical")

    publish_support_json(path, content, validate=validate, replace_existing=replace_existing)


def run_prerequisites(
    url: str, study_id: str, *, repository_root: Path, runner: CommandRunner, utc_now: Callable[[], datetime]
) -> PrerequisiteResults:
    root = repository_root.resolve()
    owned_capture_tag: str | None = None
    primary: BaseException | None = None
    try:
        require(root.is_dir(), f"repository root must be an existing directory: {root}")
        url = validate_endpoint_url(url)
        study_id = validate_study_id(study_id)
        recover_incomplete_prerequisite_rotations(root)
        begin_phase_attempt(root, study_id=study_id, url=url, phase="prerequisites")
        prerequisite_path = root / "examples" / "validation_study" / "prerequisites.json"
        config_paths = {
            name: root / "examples" / "validation_study" / "configs" / f"{name}.toml"
            for name in ("short", "streaming", "bursty")
        }
        commit_result = runner(
            ("git", "rev-parse", "HEAD"),
            cwd=root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["git_or_version"],
        )
        require(
            commit_result.returncode == 0,
            f"could not resolve clean prerequisite Git commit: {command_detail(commit_result, operation='Git commit inspection')}",
        )
        git_commit = git_commit_value(stdout_text(commit_result, operation="Git commit inspection"))
        require_clean_prerequisite_worktree(root, runner=runner)
        require(platform.python_version() == "3.12.3", "prerequisites require exact CPython 3.12.3")
        docker_version = runner(
            ("docker", "version", "--format", "{{.Server.Version}}"),
            cwd=root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["git_or_version"],
        )
        require(
            docker_version.returncode == 0,
            f"could not read Docker Engine version: {command_detail(docker_version, operation='Docker version')}",
        )
        docker_engine_version = strict_string(
            stdout_text(docker_version, operation="Docker version"), name="Docker Engine version"
        )
        compose_version = runner(
            ("docker", "compose", "version", "--short"),
            cwd=root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["git_or_version"],
        )
        require(
            compose_version.returncode == 0,
            f"could not read Docker Compose version: {command_detail(compose_version, operation='Docker Compose version')}",
        )
        docker_compose_version = strict_string(
            stdout_text(compose_version, operation="Docker Compose version"), name="Docker Compose version"
        )
        evidence_directory = (
            root / "examples" / "validation_study" / ".study-work" / "evidence" / study_id / "00-prerequisites"
        )
        evidence_directory.parent.mkdir(parents=True, exist_ok=True)
        evidence_directory.mkdir()
        mount_directory = root / "examples" / "validation_study" / ".study-work" / "mount" / study_id
        mount_directory.mkdir(parents=True)
        mount_directory.chmod(493)
        require(
            stat.S_ISDIR(mount_directory.lstat().st_mode)
            and (not stat.S_ISLNK(mount_directory.lstat().st_mode))
            and (stat.S_IMODE(mount_directory.lstat().st_mode) == 493),
            "capability mount must be a host-owned regular 0755 directory",
        )
        capture_lock = load_capture_image_lock(root / "docker" / "capture" / "image-lock.json")
        validate_capture_dockerfile(
            (root / "docker" / "capture" / "Dockerfile").read_text(encoding="utf-8"), capture_lock
        )
        pull = runner(
            ("docker", "image", "pull", TARGET_REFERENCE),
            cwd=root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["image_pull_or_build"],
        )
        require(
            pull.returncode == 0,
            f"could not pull approved target image: {command_detail(pull, operation='target image pull')}",
        )
        inspect = runner(
            ("docker", "image", "inspect", TARGET_REFERENCE),
            cwd=root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["image_pull_or_build"],
        )
        require(
            inspect.returncode == 0,
            f"could not inspect approved target image: {command_detail(inspect, operation='target image inspect')}",
        )
        inspect_stdout, _inspect_stderr = completed_output(inspect, operation="target image inspect")
        images = target_image_record(inspect_stdout)
        iid_path = evidence_directory / "capture.iid"
        require(not path_entry_exists(iid_path), "capture IID path must be absent before build")
        capture_tag = f"trafficlab-validation-{study_id}:capture"
        owned_capture_tag = capture_tag
        build = runner(
            cold_capture_build_argv(capture_tag, iid_path),
            cwd=root,
            check=False,
            capture_output=True,
            shell=False,
            timeout=SUBPROCESS_TIMEOUTS["image_pull_or_build"],
        )
        require(
            build.returncode == 0,
            f"could not build capture image: {command_detail(build, operation='capture image build')}",
        )
        try:
            capture_image_id = image_id_value(iid_path.read_text(encoding="ascii").strip(), name="capture image ID")
            require(
                capture_image_id == capture_lock.expected_capture_image_id,
                "cold capture rebuild ID must equal the checked image lock before capability validation",
            )
            iid_path.chmod(384)
            dockerfile = (root / "docker" / "capture" / "Dockerfile").read_bytes()
            capture_script = (root / "docker" / "capture" / "capture.sh").read_bytes()
        except (OSError, UnicodeDecodeError) as error:
            raise ValueError(f"could not read capture image identity or source: {error}") from error
        images.update(
            {
                "capture_image_id": capture_image_id,
                "capture_dockerfile_sha256": hashlib.sha256(dockerfile).hexdigest(),
                "capture_script_sha256": hashlib.sha256(capture_script).hexdigest(),
            }
        )
        capability = _prepare_capability(
            repository_root=root,
            study_id=study_id,
            url=url,
            evidence_directory=evidence_directory,
            mount_directory=mount_directory,
            runner=runner,
            utc_now=utc_now,
        )
        docker_command = run_prerequisite_test(
            "docker_matrix",
            docker_matrix_argv(study_id),
            repository_root=root,
            evidence_directory=evidence_directory,
            runner=runner,
            utc_now=utc_now,
        )
        internet_command = run_prerequisite_test(
            "internet_smoke",
            internet_smoke_argv(study_id, url),
            repository_root=root,
            evidence_directory=evidence_directory,
            runner=runner,
            utc_now=utc_now,
        )
        tag_to_remove = owned_capture_tag
        owned_capture_tag = None
        remove_owned_prerequisite_capture_image(tag_to_remove, repository_root=root, runner=runner)
        config_hashes: JsonObject = {}
        config_payloads: list[tuple[ExperimentConfig, Path, bytes]] = []
        for workload in workload_specs(url):
            config = build_base_config(
                workload, repository_root=root, study_id=study_id, url=url, capture_image_id=capture_image_id
            )
            content = render_checked_base_config_content(config, root)
            config_hashes[workload.name] = hashlib.sha256(content).hexdigest()
            config_payloads.append((config, config_paths[workload.name], content))
        result = PrerequisiteResults(
            schema_version=1,
            created_utc=timestamp_now(utc_now),
            study_id=study_id,
            git_commit=git_commit,
            git_tree_clean=True,
            url=url,
            tools=freeze_object(
                {
                    "docker_engine_version": docker_engine_version,
                    "docker_compose_version": docker_compose_version,
                    "host_architecture": platform.machine(),
                    "kernel_release": platform.release(),
                    "platform": platform.platform(),
                    "python_implementation": platform.python_implementation(),
                    "python_version": platform.python_version(),
                    "trafficlab_version": __version__,
                    "uv_lock_sha256": hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest(),
                }
            ),
            images=freeze_object(images),
            capability=freeze_object(capability),
            config_sha256=freeze_object(config_hashes),
            commands=(freeze_object(docker_command), freeze_object(internet_command)),
        )
        commit_prerequisite_rotation(
            root,
            prerequisite_path=prerequisite_path,
            configs=tuple(config_payloads),
            result=result,
            study_id=study_id,
            url=url,
            runner=runner,
        )
        return result
    except TrafficlabError as error:
        primary = error
        raise
    except (OSError, TypeError, ValueError, subprocess.SubprocessError) as error:
        if type(study_id) is str and STUDY_ID_PATTERN.fullmatch(study_id) is not None:
            best_effort_preserve_capability_canary(
                root / "examples" / "validation_study" / ".study-work" / "evidence" / study_id / "00-prerequisites",
                root / "examples" / "validation_study" / ".study-work" / "mount" / study_id / ".capability.headers",
            )
        primary = TrafficlabError(
            f"Validation Study prerequisite validation failed: {error}",
            corrective_action="preserve the ignored evidence, correct the prerequisite, and restart with a new study ID",
        )
        raise primary from error
    except BaseException as error:
        primary = error
        raise
    finally:
        if owned_capture_tag is not None:
            tag_to_remove = owned_capture_tag
            owned_capture_tag = None
            try:
                remove_owned_prerequisite_capture_image(tag_to_remove, repository_root=root, runner=runner)
            except BaseException as cleanup_error:
                if primary is None:
                    raise TrafficlabError(
                        f"Validation Study prerequisite capture image cleanup failed: {cleanup_error}",
                        corrective_action="preserve the prerequisite evidence, remove the exact owned capture image tag, and restart with a new study ID",
                    ) from cleanup_error
                primary.add_note(f"prerequisite capture image cleanup failed: {cleanup_error}")
