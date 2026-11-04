"""FreshForge provider for CLEWS DataLad submodule materialization workflows."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import yaml


MATERIALIZATION_PROVIDER_ID = "clews.materialization"
MATERIALIZATION_PROVIDER_VERSION = "0.0.0"

CommandRunner = Callable[[tuple[str, ...]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class _NodeContract:
    id: str
    name: str
    description: str
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    parameters: tuple[str, ...] = ("overlay",)
    artifacts: tuple[str, ...] = ()


@dataclass(frozen=True)
class MaterializationOverlay:
    """Config-driven DataLad/git-annex materialization overlay."""

    instance_root: Path
    venv_path: Path
    special_remote: str
    materialization_paths: tuple[Path, ...]
    audit_paths: tuple[Path, ...]
    report_path: Path
    submodule_path: Path | None = None
    annex_enabled: bool = True
    install_requirements: tuple[Path, ...] = ()
    install_editable_paths: tuple[Path, ...] = ()
    install_extras: tuple[str, ...] = ()
    install_packages: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "instance_root": str(self.instance_root),
            "venv_path": str(self.venv_path),
            "annex_enabled": self.annex_enabled,
            "special_remote": self.special_remote,
            "materialization_paths": [str(path) for path in self.materialization_paths],
            "audit_paths": [str(path) for path in self.audit_paths],
            "report_path": str(self.report_path),
            "install_requirements": [str(path) for path in self.install_requirements],
            "install_editable_paths": [
                str(path) for path in self.install_editable_paths
            ],
            "install_extras": list(self.install_extras),
            "install_packages": list(self.install_packages),
        }
        if self.submodule_path is not None:
            data["submodule_path"] = str(self.submodule_path)
        return data


_NODE_CONTRACTS: tuple[_NodeContract, ...] = (
    _NodeContract(
        id="check_toolchain",
        name="Check toolchain",
        description="Check Git, Python, FreshForge, DataLad, and git-annex.",
        outputs=("toolchain",),
    ),
    _NodeContract(
        id="check_python_environment",
        name="Check Python environment",
        description="Create or validate the configured Python virtual environment.",
        inputs=("toolchain",),
        outputs=("python_environment",),
    ),
    _NodeContract(
        id="install_packages",
        name="Install packages",
        description="Install configured CLEWS/FreshForge workflow packages.",
        inputs=("python_environment",),
        outputs=("packages",),
    ),
    _NodeContract(
        id="init_submodules",
        name="Initialize submodules",
        description="Initialize or update configured Git submodules.",
        inputs=("packages",),
        outputs=("submodules",),
    ),
    _NodeContract(
        id="init_annex",
        name="Initialize git-annex",
        description="Initialize git-annex in the configured DataLad repository.",
        inputs=("submodules",),
        outputs=("annex_repository",),
    ),
    _NodeContract(
        id="enable_special_remote",
        name="Enable special remote",
        description="Enable the configured git-annex special remote.",
        inputs=("annex_repository",),
        outputs=("special_remote",),
    ),
    _NodeContract(
        id="materialize_paths",
        name="Materialize required paths",
        description="Materialize configured DataLad/git-annex payload paths.",
        inputs=("special_remote",),
        outputs=("materialized_paths",),
    ),
    _NodeContract(
        id="audit_annex_availability",
        name="Audit annex availability",
        description="Audit configured payload families against the special remote.",
        inputs=("materialized_paths",),
        outputs=("annex_audit",),
    ),
    _NodeContract(
        id="write_materialization_report",
        name="Write materialization report",
        description="Write a deterministic materialization report.",
        outputs=("materialization_report",),
        artifacts=("report",),
    ),
    _NodeContract(
        id="download_era5_normals",
        name="Download ERA5 climate normals",
        description="Download ERA5 monthly climate normals (1991-2020) via CDS API.",
        inputs=("special_remote",),
        outputs=("era5_download",),
    ),
    _NodeContract(
        id="download_scanfi_species_bc",
        name="Download SCANFI species layers for BC",
        description="Download SCANFI v2 species crown-closure layers for BC from NRCan FTP.",
        inputs=("special_remote",),
        outputs=("scanfi_species_download",),
    ),
)


class ClewsMaterializationProvider:
    """FreshForge provider for CLEWS DataLad submodule materialization."""

    def __init__(self, command_runner: CommandRunner | None = None) -> None:
        self._command_runner = command_runner or _default_command_runner

    def metadata(self) -> Any:
        """Return FreshForge provider metadata."""
        node_type_metadata, provider_metadata = _freshforge_metadata_types()
        return provider_metadata(
            id=MATERIALIZATION_PROVIDER_ID,
            version=MATERIALIZATION_PROVIDER_VERSION,
            name="CLEWS materialization provider",
            description=(
                "Provider for CLEWS DataLad/git-annex submodule bootstrap and "
                "selective materialization workflows."
            ),
            node_types=tuple(
                node_type_metadata(
                    id=contract.id,
                    name=contract.name,
                    description=contract.description,
                    inputs=contract.inputs,
                    outputs=contract.outputs,
                    parameters=contract.parameters,
                    artifacts=contract.artifacts,
                )
                for contract in _NODE_CONTRACTS
            ),
        )

    def validate_node(
        self, node: Any, node_type: Any, *, location: str
    ) -> tuple[Any, ...]:
        """Validate broad materialization node shape without executing commands."""
        diagnostic, severity = _freshforge_diagnostic_types()
        diagnostics: list[Any] = []
        diagnostics.extend(
            _missing_key_diagnostics(
                diagnostic=diagnostic,
                severity=severity,
                required=tuple(node_type.inputs),
                actual=node.inputs,
                field_name="inputs",
                location=location,
            )
        )
        diagnostics.extend(
            _missing_key_diagnostics(
                diagnostic=diagnostic,
                severity=severity,
                required=tuple(node_type.outputs),
                actual=node.outputs,
                field_name="outputs",
                location=location,
            )
        )
        diagnostics.extend(
            _missing_key_diagnostics(
                diagnostic=diagnostic,
                severity=severity,
                required=tuple(node_type.parameters),
                actual=node.parameters,
                field_name="parameters",
                location=location,
            )
        )
        artifacts = node.artifacts if isinstance(node.artifacts, dict) else {}
        diagnostics.extend(
            _missing_key_diagnostics(
                diagnostic=diagnostic,
                severity=severity,
                required=tuple(node_type.artifacts),
                actual=artifacts,
                field_name="artifacts",
                location=location,
            )
        )
        overlay_path = _optional_parameter(node, "overlay")
        if overlay_path:
            overlay, overlay_errors = load_materialization_overlay(Path(overlay_path))
            diagnostics.extend(
                _overlay_diagnostics(
                    diagnostic=diagnostic,
                    severity=severity,
                    errors=overlay_errors,
                    location=f"{location}.parameters.overlay",
                )
            )
            if overlay is not None and node_type.id == "write_materialization_report":
                if not str(overlay.report_path).strip():
                    diagnostics.append(
                        diagnostic(
                            severity=severity.ERROR,
                            code="clews.materialization.overlay.report_missing",
                            message="Materialization overlay requires report.path.",
                            location=f"{location}.parameters.overlay",
                        )
                    )
        return tuple(diagnostics)

    def run_node(self, node: Any, node_type: Any, *, context: Any) -> Any:
        """Run one materialization node through the FreshForge provider API."""
        result_type, run_status = _freshforge_run_result_types()
        diagnostic, severity = _freshforge_diagnostic_types()
        overlay, overlay_errors = load_materialization_overlay(
            Path(_parameter(node, "overlay"))
        )
        if overlay is None:
            return result_type(
                status=run_status.FAILED,
                diagnostics=tuple(
                    diagnostic(
                        severity=severity.ERROR,
                        code="clews.materialization.overlay.invalid",
                        message=error,
                        location=f"nodes.{node.id}.parameters.overlay",
                    )
                    for error in overlay_errors
                ),
            )
        if str(node_type.id) == "write_materialization_report":
            return _write_report_result(
                node=node,
                node_type=node_type,
                context=context,
                overlay=overlay,
                result_type=result_type,
                run_status=run_status,
            )

        commands = _commands_for_node(str(node_type.id), overlay)
        if commands is None:
            return result_type(
                status=run_status.FAILED,
                diagnostics=(
                    diagnostic(
                        severity=severity.ERROR,
                        code="clews.materialization.execution.unsupported",
                        message=(
                            "CLEWS materialization provider has no execution hook "
                            f"for node type '{node_type.id}'."
                        ),
                        location=f"nodes.{node.id}",
                    ),
                ),
            )
        if _is_annex_disabled_noop(str(node_type.id), overlay):
            return result_type(
                status=run_status.SUCCESS,
                outputs=node.outputs if isinstance(node.outputs, dict) else {},
                artifacts=_resolved_artifacts(node, context),
                data={
                    "annex_enabled": False,
                    "commands": [],
                    "skipped": (
                        "Annex operation skipped because annex.enabled is false "
                        "in the materialization overlay."
                    ),
                },
            )
        completed = [self._command_runner(command) for command in commands]
        diagnostics = _command_diagnostics(
            completed=completed,
            diagnostic=diagnostic,
            severity=severity,
            node_id=node.id,
            node_type=str(node_type.id),
        )
        status = run_status.FAILED if diagnostics else run_status.SUCCESS
        return result_type(
            status=status,
            outputs=node.outputs if isinstance(node.outputs, dict) else {},
            artifacts=_resolved_artifacts(node, context),
            diagnostics=tuple(diagnostics),
            data={
                "commands": [list(command) for command in commands],
                "returncodes": [item.returncode for item in completed],
                "stdout": [item.stdout for item in completed if item.stdout],
                "stderr": [item.stderr for item in completed if item.stderr],
            },
        )


def provider_factory() -> ClewsMaterializationProvider:
    """Return the CLEWS materialization provider for entry-point discovery."""
    return ClewsMaterializationProvider()


def load_materialization_overlay(
    path: Path,
) -> tuple[MaterializationOverlay | None, tuple[str, ...]]:
    """Load and validate a materialization overlay YAML document."""
    if not path.exists():
        return None, (f"Materialization overlay does not exist: {path}",)
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return None, (f"Could not read materialization overlay '{path}': {exc}",)
    if not isinstance(payload, Mapping):
        return None, ("Materialization overlay must be a mapping.",)

    errors: list[str] = []
    instance = _mapping(payload.get("instance"), "instance", errors)
    environment = _mapping(payload.get("environment"), "environment", errors)
    install = _mapping(payload.get("install"), "install", errors)
    annex = _mapping(payload.get("annex"), "annex", errors)
    materialization = _mapping(
        payload.get("materialization"), "materialization", errors
    )
    audit = _mapping(payload.get("audit"), "audit", errors)
    report = _mapping(payload.get("report"), "report", errors)
    if errors:
        return None, tuple(errors)

    instance_root = _required_path(instance, "root", "instance.root", errors)
    venv_path = _required_path(
        environment, "venv_path", "environment.venv_path", errors
    )
    annex_enabled = _optional_bool(
        annex, "enabled", "annex.enabled", errors, default=True
    )
    special_remote = (
        _required_str(annex, "special_remote", "annex.special_remote", errors)
        if annex_enabled
        else _optional_str(annex.get("special_remote"))
    )
    report_path = _required_path(report, "path", "report.path", errors)
    materialization_paths = _path_list(
        materialization, "required_paths", "materialization.required_paths", errors
    )
    audit_paths = _path_list(audit, "required_paths", "audit.required_paths", errors)
    if errors:
        return None, tuple(errors)

    install_requirements = _path_list(
        install, "requirements", "install.requirements", errors
    )
    install_editable_paths = _path_list(
        install, "editable_paths", "install.editable_paths", errors
    )
    install_extras = _str_list(install, "extras", "install.extras", errors)
    install_packages = _str_list(install, "packages", "install.packages", errors)
    if errors:
        return None, tuple(errors)

    return (
        MaterializationOverlay(
            instance_root=instance_root,
            submodule_path=_optional_path(instance.get("submodule_path")),
            venv_path=venv_path,
            annex_enabled=annex_enabled,
            special_remote=special_remote,
            materialization_paths=tuple(materialization_paths),
            audit_paths=tuple(audit_paths),
            report_path=report_path,
            install_requirements=tuple(install_requirements),
            install_editable_paths=tuple(install_editable_paths),
            install_extras=tuple(install_extras),
            install_packages=tuple(install_packages),
        ),
        (),
    )


def _commands_for_node(
    node_type_id: str,
    overlay: MaterializationOverlay,
) -> tuple[tuple[str, ...], ...] | None:
    builders: dict[
        str, Callable[[MaterializationOverlay], tuple[tuple[str, ...], ...]]
    ] = {
        "check_toolchain": _check_toolchain_commands,
        "check_python_environment": _python_environment_commands,
        "install_packages": _install_package_commands,
        "init_submodules": _init_submodule_commands,
        "init_annex": _init_annex_commands,
        "enable_special_remote": _enable_remote_commands,
        "materialize_paths": _materialize_path_commands,
        "audit_annex_availability": _audit_annex_commands,
        "download_era5_normals": _download_era5_normals_commands,
        "download_scanfi_species_bc": _download_scanfi_species_bc_commands,
    }
    builder = builders.get(node_type_id)
    return None if builder is None else builder(overlay)


def _check_toolchain_commands(
    overlay: MaterializationOverlay,
) -> tuple[tuple[str, ...], ...]:
    commands = [
        ("git", "--version"),
        (sys.executable, "--version"),
        (sys.executable, "-m", "freshforge", "--version"),
    ]
    if overlay.annex_enabled:
        commands.extend(
            [
                (_venv_executable(overlay.venv_path, "datalad"), "--version"),
                ("git", "annex", "version"),
            ]
        )
    return tuple(commands)


def _python_environment_commands(
    overlay: MaterializationOverlay,
) -> tuple[tuple[str, ...], ...]:
    python = _venv_python(overlay.venv_path)
    if Path(python).exists():
        return ((python, "--version"),)
    return ((sys.executable, "-m", "venv", str(overlay.venv_path)),)


def _install_package_commands(
    overlay: MaterializationOverlay,
) -> tuple[tuple[str, ...], ...]:
    python = _venv_python(overlay.venv_path)
    commands: list[tuple[str, ...]] = []
    if overlay.install_extras:
        commands.append(
            (
                python,
                "-m",
                "pip",
                "install",
                "-e",
                f".[{','.join(overlay.install_extras)}]",
            )
        )
    for requirement in overlay.install_requirements:
        commands.append((python, "-m", "pip", "install", "-r", str(requirement)))
    for editable_path in overlay.install_editable_paths:
        commands.append((python, "-m", "pip", "install", "-e", str(editable_path)))
    for package in overlay.install_packages:
        commands.append((python, "-m", "pip", "install", package))
    return tuple(commands)


def _init_submodule_commands(
    overlay: MaterializationOverlay,
) -> tuple[tuple[str, ...], ...]:
    command = ["git", "submodule", "update", "--init", "--recursive"]
    if overlay.submodule_path is not None:
        command.append(str(overlay.submodule_path))
    return (tuple(command),)


def _init_annex_commands(
    overlay: MaterializationOverlay,
) -> tuple[tuple[str, ...], ...]:
    if not overlay.annex_enabled:
        return ()
    return (("git", "-C", str(overlay.instance_root), "annex", "init"),)


def _enable_remote_commands(
    overlay: MaterializationOverlay,
) -> tuple[tuple[str, ...], ...]:
    if not overlay.annex_enabled:
        return ()
    return (
        (
            "git",
            "-C",
            str(overlay.instance_root),
            "annex",
            "enableremote",
            overlay.special_remote,
        ),
    )


def _materialize_path_commands(
    overlay: MaterializationOverlay,
) -> tuple[tuple[str, ...], ...]:
    if not overlay.annex_enabled:
        return tuple(
            _path_exists_command(_instance_path(overlay, path))
            for path in overlay.materialization_paths
        )
    return tuple(
        (
            _venv_executable(overlay.venv_path, "datalad"),
            "get",
            "-r",
            str(_instance_path(overlay, path)),
        )
        for path in overlay.materialization_paths
    )


def _audit_annex_commands(
    overlay: MaterializationOverlay,
) -> tuple[tuple[str, ...], ...]:
    if not overlay.annex_enabled:
        return ()
    return tuple(
        (
            "git",
            "-C",
            str(overlay.instance_root),
            "annex",
            "find",
            "--not",
            "--in",
            overlay.special_remote,
            "--",
            str(path),
        )
        for path in overlay.audit_paths
    )


def _download_era5_normals_commands(
    overlay: MaterializationOverlay,
) -> tuple[tuple[str, ...], ...]:
    """Run ERA5 climate normals download script."""
    script_path = overlay.instance_root.parent / "scripts" / "download_era5_normals.py"
    if not script_path.exists():
        # Fallback to relative path from repo root
        script_path = Path("scripts/download_era5_normals.py")
    python = _venv_python(overlay.venv_path)
    output_dir = _instance_path(overlay, Path("raw/ecmwf/era5/normals"))
    return (
        (
            python,
            str(script_path),
            "--output-dir",
            str(output_dir),
        ),
    )


def _download_scanfi_species_bc_commands(
    overlay: MaterializationOverlay,
) -> tuple[tuple[str, ...], ...]:
    """Run SCANFI species layers download script."""
    script_path = overlay.instance_root.parent / "scripts" / "download_scanfi_species_bc.py"
    if not script_path.exists():
        # Fallback to relative path from repo root
        script_path = Path("scripts/download_scanfi_species_bc.py")
    python = _venv_python(overlay.venv_path)
    output_dir = _instance_path(overlay, Path("raw/nrcan/scanfi_v2/species"))
    return (
        (
            python,
            str(script_path),
            "--output-dir",
            str(output_dir),
        ),
    )


def _path_exists_command(path: Path) -> tuple[str, ...]:
    return (
        sys.executable,
        "-c",
        "from pathlib import Path; import sys; sys.exit(0 if Path(sys.argv[1]).exists() else 1)",
        str(path),
    )


def _is_annex_disabled_noop(
    node_type_id: str,
    overlay: MaterializationOverlay,
) -> bool:
    return not overlay.annex_enabled and node_type_id in {
        "init_annex",
        "enable_special_remote",
        "audit_annex_availability",
    }


def _write_report_result(
    *,
    node: Any,
    node_type: Any,
    context: Any,
    overlay: MaterializationOverlay,
    result_type: Any,
    run_status: Any,
) -> Any:
    report_path = _resolve_run_path(context, overlay.report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "workflow_id": getattr(context, "workflow_id", None),
        "provider_id": MATERIALIZATION_PROVIDER_ID,
        "node_id": node.id,
        "overlay": overlay.to_dict(),
        "completed_outputs": getattr(context, "completed_outputs", {}),
        "completed_artifacts": getattr(context, "completed_artifacts", {}),
    }
    report_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    artifacts = _resolved_artifacts(node, context)
    artifacts.setdefault("report", str(report_path))
    return result_type(
        status=run_status.SUCCESS,
        outputs=node.outputs if isinstance(node.outputs, dict) else {},
        artifacts=artifacts,
        data={"report_path": str(report_path)},
    )


def _command_diagnostics(
    *,
    completed: Sequence[subprocess.CompletedProcess[str]],
    diagnostic: Any,
    severity: Any,
    node_id: str,
    node_type: str,
) -> list[Any]:
    diagnostics: list[Any] = []
    for index, item in enumerate(completed, start=1):
        if item.returncode != 0:
            diagnostics.append(
                diagnostic(
                    severity=severity.ERROR,
                    code="clews.materialization.command.failed",
                    message=(
                        f"Materialization command {index} for node '{node_id}' "
                        f"exited with return code {item.returncode}."
                    ),
                    location=f"nodes.{node_id}",
                )
            )
        if node_type == "audit_annex_availability" and item.stdout.strip():
            diagnostics.append(
                diagnostic(
                    severity=severity.ERROR,
                    code="clews.materialization.audit.missing_remote_content",
                    message=(
                        "Annex audit found paths that are not present in the "
                        "configured special remote."
                    ),
                    location=f"nodes.{node_id}",
                )
            )
    return diagnostics


def _mapping(value: object, field_name: str, errors: list[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        errors.append(f"Materialization overlay requires mapping field '{field_name}'.")
        return {}
    return value


def _required_path(
    payload: Mapping[str, object],
    key: str,
    label: str,
    errors: list[str],
) -> Path:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"Materialization overlay requires nonempty '{label}'.")
        return Path("")
    return Path(value)


def _required_str(
    payload: Mapping[str, object],
    key: str,
    label: str,
    errors: list[str],
) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"Materialization overlay requires nonempty '{label}'.")
        return ""
    return value


def _optional_path(value: object) -> Path | None:
    if isinstance(value, str) and value.strip():
        return Path(value)
    return None


def _optional_str(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return value
    return ""


def _optional_bool(
    payload: Mapping[str, object],
    key: str,
    label: str,
    errors: list[str],
    *,
    default: bool,
) -> bool:
    value = payload.get(key, default)
    if isinstance(value, bool):
        return value
    errors.append(f"Materialization overlay field '{label}' must be a boolean.")
    return default


def _path_list(
    payload: Mapping[str, object],
    key: str,
    label: str,
    errors: list[str],
) -> list[Path]:
    values = payload.get(key)
    if not isinstance(values, list):
        errors.append(f"Materialization overlay requires list field '{label}'.")
        return []
    paths: list[Path] = []
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value.strip():
            errors.append(
                f"Materialization overlay field '{label}[{index}]' is invalid."
            )
        else:
            paths.append(Path(value))
    return paths


def _str_list(
    payload: Mapping[str, object],
    key: str,
    label: str,
    errors: list[str],
) -> list[str]:
    values = payload.get(key, [])
    if not isinstance(values, list):
        errors.append(f"Materialization overlay field '{label}' must be a list.")
        return []
    strings: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value.strip():
            errors.append(
                f"Materialization overlay field '{label}[{index}]' is invalid."
            )
        else:
            strings.append(value)
    return strings


def _overlay_diagnostics(
    *,
    diagnostic: Any,
    severity: Any,
    errors: Sequence[str],
    location: str,
) -> tuple[Any, ...]:
    return tuple(
        diagnostic(
            severity=severity.ERROR,
            code="clews.materialization.overlay.invalid",
            message=error,
            location=location,
        )
        for error in errors
    )


def _missing_key_diagnostics(
    *,
    diagnostic: Any,
    severity: Any,
    required: Sequence[str],
    actual: dict[str, object],
    field_name: str,
    location: str,
) -> tuple[Any, ...]:
    diagnostics: list[Any] = []
    for key in required:
        if key not in actual:
            diagnostics.append(
                diagnostic(
                    severity=severity.ERROR,
                    code="clews.materialization.parameters.missing",
                    message=f"CLEWS materialization node requires {field_name}.{key}.",
                    location=location,
                )
            )
    return tuple(diagnostics)


def _optional_parameter(node: Any, key: str) -> str:
    parameters = node.parameters if isinstance(node.parameters, dict) else {}
    value = parameters.get(key)
    return value if isinstance(value, str) else ""


def _parameter(node: Any, key: str) -> str:
    value = _optional_parameter(node, key)
    if not value:
        raise ValueError(f"Missing required materialization parameter: {key}")
    return value


def _instance_path(overlay: MaterializationOverlay, path: Path) -> Path:
    return path if path.is_absolute() else overlay.instance_root / path


def _venv_python(venv_path: Path) -> str:
    if sys.platform.startswith("win"):
        return str(venv_path / "Scripts" / "python.exe")
    return str(venv_path / "bin" / "python")


def _venv_executable(venv_path: Path, name: str) -> str:
    if sys.platform.startswith("win"):
        return str(venv_path / "Scripts" / f"{name}.exe")
    return str(venv_path / "bin" / name)


def _default_command_runner(
    command: tuple[str, ...],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )


def _resolve_run_path(context: Any, path: Path) -> Path:
    workdir = Path(getattr(context, "workdir", Path(".")))
    namespace = getattr(context, "run_namespace", None)
    return workdir / namespace / path if namespace else workdir / path


def _resolved_artifacts(node: Any, context: Any) -> dict[str, str]:
    artifacts = node.artifacts if isinstance(node.artifacts, dict) else {}
    return {
        key: str(_resolve_run_path(context, Path(value)))
        for key, value in artifacts.items()
        if isinstance(value, str)
    }


def _freshforge_metadata_types() -> tuple[type[Any], type[Any]]:
    from freshforge.providers import NodeTypeMetadata, ProviderMetadata

    return NodeTypeMetadata, ProviderMetadata


def _freshforge_diagnostic_types() -> tuple[type[Any], Any]:
    from freshforge.records import Diagnostic, DiagnosticSeverity

    return Diagnostic, DiagnosticSeverity


def _freshforge_run_result_types() -> tuple[type[Any], Any]:
    from freshforge.records import ProviderRunResult, RunStatus

    return ProviderRunResult, RunStatus
