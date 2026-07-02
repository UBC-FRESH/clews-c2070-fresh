from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib
import unittest

from fresh_fibre.freshforge_materialization import (
    MATERIALIZATION_PROVIDER_ID,
    ClewsMaterializationProvider,
    load_materialization_overlay,
    provider_factory,
)

try:
    import freshforge  # noqa: F401
except Exception:  # pragma: no cover - exercised only when dependency is absent
    freshforge = None


OVERLAY_PATH = Path("examples/freshforge/materialization_overlay.yaml")
SMOKE_WORKFLOW_PATH = Path("examples/freshforge/materialization_smoke_workflow.yaml")
DATA_WORKFLOW_PATH = Path("examples/freshforge/data_materialization_workflow.yaml")
COMMAND_NODE_TYPES = (
    "check_toolchain",
    "check_python_environment",
    "install_packages",
    "init_submodules",
    "init_annex",
    "enable_special_remote",
    "materialize_paths",
    "audit_annex_availability",
)


def _successful_runner(commands: list[tuple[str, ...]]):
    def _run(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=0,
            stdout="",
            stderr="",
        )

    return _run


def _failing_runner(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=list(command),
        returncode=7,
        stdout="",
        stderr="failed",
    )


def _audit_missing_runner(command: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=list(command),
        returncode=0,
        stdout="metadata/missing.csv\n",
        stderr="",
    )


@unittest.skipIf(freshforge is None, "freshforge is not installed")
def _workflow_node(node_type_id: str, *, overlay: str | None = None):
    from freshforge.records import WorkflowNode

    output_key_by_node_type = {
        "check_toolchain": "toolchain",
        "check_python_environment": "python_environment",
        "install_packages": "packages",
        "init_submodules": "submodules",
        "init_annex": "annex_repository",
        "enable_special_remote": "special_remote",
        "materialize_paths": "materialized_paths",
        "audit_annex_availability": "annex_audit",
        "write_materialization_report": "materialization_report",
    }
    return WorkflowNode(
        id=node_type_id,
        provider=f"clews.materialization.{node_type_id}",
        outputs={output_key_by_node_type[node_type_id]: "ok"},
        parameters={"overlay": overlay or str(OVERLAY_PATH)},
        artifacts=(
            {"report": "runtime/freshforge/materialization_report.json"}
            if node_type_id == "write_materialization_report"
            else {}
        ),
    )


def _node_type(provider: ClewsMaterializationProvider, node_type_id: str):
    return next(
        item for item in provider.metadata().node_types if item.id == node_type_id
    )


@unittest.skipIf(freshforge is None, "freshforge is not installed")
class FreshForgeMaterializationTest(unittest.TestCase):
    def test_materialization_provider_metadata_serializes_deterministically(
        self,
    ) -> None:
        metadata = provider_factory().metadata()

        payload = metadata.to_dict()
        self.assertEqual(payload["id"], MATERIALIZATION_PROVIDER_ID)
        self.assertEqual(
            [item["id"] for item in payload["node_types"]],
            [
                "check_toolchain",
                "check_python_environment",
                "install_packages",
                "init_submodules",
                "init_annex",
                "enable_special_remote",
                "materialize_paths",
                "audit_annex_availability",
                "write_materialization_report",
            ],
        )
        self.assertTrue(
            all(item["parameters"] == ["overlay"] for item in payload["node_types"])
        )

    def test_pyproject_declares_materialization_provider_entry_point(self) -> None:
        pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
        entry_points = pyproject["project"]["entry-points"]["freshforge.providers"]

        self.assertEqual(
            entry_points["clews.materialization"],
            "fresh_fibre.freshforge_materialization:provider_factory",
        )

    def test_overlay_parser_accepts_clews_fixture(self) -> None:
        overlay, diagnostics = load_materialization_overlay(OVERLAY_PATH)

        self.assertEqual(diagnostics, ())
        self.assertIsNotNone(overlay)
        assert overlay is not None
        self.assertTrue(overlay.annex_enabled)
        self.assertEqual(overlay.instance_root, Path("data"))
        self.assertEqual(overlay.submodule_path, Path("data"))
        self.assertEqual(overlay.special_remote, "arbutus-s3")
        self.assertEqual(overlay.materialization_paths, (Path("metadata"), Path("manifests")))
        self.assertEqual(overlay.audit_paths, (Path("metadata"), Path("manifests")))

    def test_overlay_parser_accepts_plain_git_overlay_without_special_remote(
        self,
    ) -> None:
        overlay_path = Path(os.environ.get("TMPDIR", "/tmp")) / "plain_git_overlay.yaml"
        overlay_path.write_text(
            "\n".join(
                [
                    "instance:",
                    "  root: data",
                    "environment:",
                    "  venv_path: .venv",
                    "install:",
                    "  requirements: []",
                    "  editable_paths: []",
                    "  extras: []",
                    "  packages: []",
                    "annex:",
                    "  enabled: false",
                    "materialization:",
                    "  required_paths: [metadata, manifests]",
                    "audit:",
                    "  required_paths: [metadata, manifests]",
                    "report:",
                    "  path: runtime/freshforge/report.json",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        overlay, diagnostics = load_materialization_overlay(overlay_path)

        self.assertEqual(diagnostics, ())
        self.assertIsNotNone(overlay)
        assert overlay is not None
        self.assertFalse(overlay.annex_enabled)
        self.assertEqual(overlay.special_remote, "")

    def test_overlay_parser_requires_special_remote_when_annex_enabled(self) -> None:
        overlay_path = Path(os.environ.get("TMPDIR", "/tmp")) / "annex_overlay.yaml"
        overlay_path.write_text(
            "\n".join(
                [
                    "instance:",
                    "  root: data",
                    "environment:",
                    "  venv_path: .venv",
                    "install:",
                    "  requirements: []",
                    "  editable_paths: []",
                    "  extras: []",
                    "  packages: []",
                    "annex:",
                    "  enabled: true",
                    "materialization:",
                    "  required_paths: [metadata]",
                    "audit:",
                    "  required_paths: [metadata]",
                    "report:",
                    "  path: runtime/freshforge/report.json",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        overlay, diagnostics = load_materialization_overlay(overlay_path)

        self.assertIsNone(overlay)
        self.assertTrue(any("annex.special_remote" in item for item in diagnostics))

    def test_overlay_parser_reports_missing_required_fields(self) -> None:
        overlay_path = Path(os.environ.get("TMPDIR", "/tmp")) / "bad_overlay.yaml"
        overlay_path.write_text("instance: {}\n", encoding="utf-8")

        overlay, diagnostics = load_materialization_overlay(overlay_path)

        self.assertIsNone(overlay)
        self.assertTrue(any("environment" in item for item in diagnostics))
        self.assertTrue(any("annex" in item for item in diagnostics))

    def test_provider_validation_reports_missing_overlay_parameter(self) -> None:
        provider = provider_factory()
        node_type = _node_type(provider, "check_toolchain")
        node = _workflow_node("check_toolchain")
        node = type(node)(
            id=node.id,
            provider=node.provider,
            outputs=node.outputs,
            parameters={},
        )

        diagnostics = provider.validate_node(node, node_type, location="nodes.0")

        self.assertEqual(
            {diagnostic.code for diagnostic in diagnostics},
            {"clews.materialization.parameters.missing"},
        )

    def test_provider_validation_reports_bad_overlay_path(self) -> None:
        provider = provider_factory()
        node_type = _node_type(provider, "check_toolchain")
        node = _workflow_node("check_toolchain", overlay="missing.yaml")

        diagnostics = provider.validate_node(node, node_type, location="nodes.0")

        self.assertTrue(
            any(
                diagnostic.code == "clews.materialization.overlay.invalid"
                for diagnostic in diagnostics
            )
        )

    def test_materialization_command_nodes_use_mocked_runner(self) -> None:
        from freshforge.execution import RunContext
        from freshforge.records import RunStatus

        for node_type_id in COMMAND_NODE_TYPES:
            with self.subTest(node_type_id=node_type_id):
                commands: list[tuple[str, ...]] = []
                provider = ClewsMaterializationProvider(
                    command_runner=_successful_runner(commands)
                )
                node = _workflow_node(node_type_id)

                result = provider.run_node(
                    node,
                    _node_type(provider, node_type_id),
                    context=RunContext(workflow_id="wf", workdir=Path("/tmp")),
                )

                self.assertIs(result.status, RunStatus.SUCCESS)
                self.assertTrue(commands)
                self.assertEqual(
                    result.data["commands"], [list(command) for command in commands]
                )

    def test_materialization_command_failure_returns_diagnostic(self) -> None:
        from freshforge.execution import RunContext
        from freshforge.records import RunStatus

        provider = ClewsMaterializationProvider(command_runner=_failing_runner)
        node = _workflow_node("init_annex")

        result = provider.run_node(
            node,
            _node_type(provider, "init_annex"),
            context=RunContext(workflow_id="wf", workdir=Path("/tmp")),
        )

        self.assertIs(result.status, RunStatus.FAILED)
        self.assertEqual(
            {diagnostic.code for diagnostic in result.diagnostics},
            {"clews.materialization.command.failed"},
        )

    def test_materialization_commands_match_data_submodule_contract(self) -> None:
        from freshforge.execution import RunContext

        expected = {
            "init_submodules": [
                ("git", "submodule", "update", "--init", "--recursive", "data")
            ],
            "init_annex": [("git", "-C", "data", "annex", "init")],
            "enable_special_remote": [
                ("git", "-C", "data", "annex", "enableremote", "arbutus-s3")
            ],
            "materialize_paths": [
                (".venv/bin/datalad", "get", "-r", "data/metadata"),
                (".venv/bin/datalad", "get", "-r", "data/manifests"),
            ],
        }
        for node_type_id, expected_commands in expected.items():
            with self.subTest(node_type_id=node_type_id):
                commands: list[tuple[str, ...]] = []
                provider = ClewsMaterializationProvider(
                    command_runner=_successful_runner(commands)
                )
                node = _workflow_node(node_type_id)

                provider.run_node(
                    node,
                    _node_type(provider, node_type_id),
                    context=RunContext(workflow_id="wf", workdir=Path("/tmp")),
                )

                self.assertEqual(commands, expected_commands)

    def test_annex_audit_stdout_is_failure_even_with_zero_returncode(self) -> None:
        from freshforge.execution import RunContext
        from freshforge.records import RunStatus

        provider = ClewsMaterializationProvider(command_runner=_audit_missing_runner)
        node = _workflow_node("audit_annex_availability")

        result = provider.run_node(
            node,
            _node_type(provider, "audit_annex_availability"),
            context=RunContext(workflow_id="wf", workdir=Path("/tmp")),
        )

        self.assertIs(result.status, RunStatus.FAILED)
        self.assertEqual(
            {diagnostic.code for diagnostic in result.diagnostics},
            {"clews.materialization.audit.missing_remote_content"},
        )

    def test_annex_disabled_nodes_are_noop_success(self) -> None:
        from freshforge.execution import RunContext
        from freshforge.records import RunStatus

        overlay_path = Path(os.environ.get("TMPDIR", "/tmp")) / "plain_git_overlay_noop.yaml"
        overlay_path.write_text(
            "\n".join(
                [
                    "instance:",
                    "  root: data",
                    "environment:",
                    "  venv_path: .venv",
                    "install:",
                    "  requirements: []",
                    "  editable_paths: []",
                    "  extras: []",
                    "  packages: []",
                    "annex:",
                    "  enabled: false",
                    "materialization:",
                    "  required_paths: [metadata]",
                    "audit:",
                    "  required_paths: [metadata]",
                    "report:",
                    "  path: runtime/freshforge/report.json",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        for node_type_id in (
            "init_annex",
            "enable_special_remote",
            "audit_annex_availability",
        ):
            with self.subTest(node_type_id=node_type_id):
                commands: list[tuple[str, ...]] = []
                provider = ClewsMaterializationProvider(
                    command_runner=_successful_runner(commands)
                )
                node = _workflow_node(node_type_id, overlay=str(overlay_path))

                result = provider.run_node(
                    node,
                    _node_type(provider, node_type_id),
                    context=RunContext(workflow_id="wf", workdir=Path("/tmp")),
                )

                self.assertIs(result.status, RunStatus.SUCCESS)
                self.assertEqual(commands, [])
                self.assertFalse(result.data["annex_enabled"])

    def test_plain_git_materialize_paths_checks_working_tree_paths(self) -> None:
        from freshforge.execution import RunContext
        from freshforge.records import RunStatus

        overlay_path = Path(os.environ.get("TMPDIR", "/tmp")) / "plain_git_materialize.yaml"
        overlay_path.write_text(
            "\n".join(
                [
                    "instance:",
                    "  root: data",
                    "environment:",
                    "  venv_path: .venv",
                    "install:",
                    "  requirements: []",
                    "  editable_paths: []",
                    "  extras: []",
                    "  packages: []",
                    "annex:",
                    "  enabled: false",
                    "materialization:",
                    "  required_paths: [metadata]",
                    "audit:",
                    "  required_paths: [metadata]",
                    "report:",
                    "  path: runtime/freshforge/report.json",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        commands: list[tuple[str, ...]] = []
        provider = ClewsMaterializationProvider(command_runner=_successful_runner(commands))
        node = _workflow_node("materialize_paths", overlay=str(overlay_path))

        result = provider.run_node(
            node,
            _node_type(provider, "materialize_paths"),
            context=RunContext(workflow_id="wf", workdir=Path("/tmp")),
        )

        self.assertIs(result.status, RunStatus.SUCCESS)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0:2], (sys.executable, "-c"))
        self.assertEqual(commands[0][-1], "data/metadata")

    def test_write_report_node_resolves_namespace_artifact(self) -> None:
        from freshforge.execution import RunContext
        from freshforge.records import RunStatus

        provider = provider_factory()
        node = _workflow_node("write_materialization_report")
        workdir = Path(os.environ.get("TMPDIR", "/tmp")) / "clews_ff_report_test"

        result = provider.run_node(
            node,
            _node_type(provider, "write_materialization_report"),
            context=RunContext(
                workflow_id="wf",
                workdir=workdir,
                run_namespace="smoke",
                completed_outputs={"check": {"ok": True}},
            ),
        )

        report_path = (
            workdir / "smoke" / "runtime" / "freshforge" / "materialization_report.json"
        )
        self.assertIs(result.status, RunStatus.SUCCESS)
        self.assertEqual(result.artifacts["report"], str(report_path))
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["provider_id"], MATERIALIZATION_PROVIDER_ID)
        self.assertEqual(payload["completed_outputs"], {"check": {"ok": True}})

    def test_materialization_workflows_validate_and_plan(self) -> None:
        from freshforge.loading import load_workflow
        from freshforge.planning import create_run_plan
        from freshforge.validation import validate_workflow_with_providers

        for workflow_path in (SMOKE_WORKFLOW_PATH, DATA_WORKFLOW_PATH):
            with self.subTest(workflow_path=workflow_path):
                spec, diagnostics = load_workflow(workflow_path)
                self.assertIsNotNone(spec)
                diagnostics = validate_workflow_with_providers(
                    spec,
                    structural_diagnostics=diagnostics,
                )
                plan = create_run_plan(spec, diagnostics=diagnostics)

                self.assertFalse(plan.has_errors)

    def test_materialization_fixture_cli_run_writes_report(self) -> None:
        workdir = Path(os.environ.get("TMPDIR", "/tmp")) / "clews_ff_cli_test"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "freshforge",
                "run",
                str(SMOKE_WORKFLOW_PATH),
                "--workdir",
                str(workdir),
                "--namespace",
                "smoke",
                "--json",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["summary"]["succeeded_count"], 1)
        self.assertTrue(
            (
                workdir
                / "smoke"
                / "runtime"
                / "freshforge"
                / "materialization_report.json"
            ).exists()
        )


if __name__ == "__main__":
    unittest.main()
