"""Packaging / adapter contract tests that do NOT need the Dify SDK.

These are static consistency checks plus graceful-degradation checks for the
Dify adapter. They prove the repository is internally coherent; they do NOT
prove the plugin runs inside Dify (see README "已知限制").
"""

from __future__ import annotations

import unittest
from pathlib import Path

from adapters.dify_model import (
    DifyModelClient,
    _content_of,
    dify_sdk_available,
    dify_session_llm,
)
from core.errors import LLMError, LLMUnavailable

ROOT = Path(__file__).resolve().parent.parent

TOOL_NAMES = ["resolve_metric", "arbitrate", "record_outcome", "list_metrics"]


class AdapterDegradationTest(unittest.TestCase):
    def test_sdk_detection_returns_bool(self) -> None:
        self.assertIsInstance(dify_sdk_available(), bool)

    def test_missing_session_raises_llm_unavailable(self) -> None:
        with self.assertRaises(LLMUnavailable):
            DifyModelClient(session=None).complete("hello")

    def test_session_without_model_gateway_raises(self) -> None:
        class Dummy:
            pass

        with self.assertRaises(LLMUnavailable):
            DifyModelClient(session=Dummy()).complete("hello")

    def test_session_without_invoke_raises(self) -> None:
        class LLM:
            pass

        class Model:
            llm = LLM()

        class Session:
            model = Model()

        with self.assertRaises(LLMUnavailable):
            DifyModelClient(session=Session()).complete("hello")

    @unittest.skipIf(dify_sdk_available(), "dify_plugin SDK installed: import succeeds")
    def test_import_error_is_reported_as_llm_unavailable(self) -> None:
        class LLM:
            def invoke(self, *args, **kwargs):  # pragma: no cover - never reached
                raise AssertionError("should not invoke without SDK")

        class Model:
            llm = LLM()

        class Session:
            model = Model()

        with self.assertRaises(LLMUnavailable) as ctx:
            DifyModelClient(session=Session()).complete("hello")
        self.assertIn("dify_plugin", str(ctx.exception))

    def test_session_factory(self) -> None:
        self.assertIsNone(dify_session_llm(None))
        client = dify_session_llm(object(), {"model_name": "gpt-4o-mini", "model_provider": "openai"})
        self.assertEqual(client.name, "dify-session-model:gpt-4o-mini")
        self.assertEqual(client.model_provider, "openai")

    def test_content_extraction(self) -> None:
        self.assertEqual(_content_of("plain"), "plain")
        self.assertEqual(_content_of({"content": "dict"}), "dict")

        class Message:
            content = "message"

        class Result:
            message = Message()

        self.assertEqual(_content_of(Result()), "message")
        with self.assertRaises(LLMError):
            _content_of(object())


class ManifestContractTest(unittest.TestCase):
    """Static checks: every referenced file exists and names agree."""

    def test_manifest_references_existing_provider(self) -> None:
        manifest = (ROOT / "manifest.yaml").read_text(encoding="utf-8")
        self.assertIn("version: 0.1.0", manifest)
        self.assertIn("type: plugin", manifest)
        self.assertIn("provider/metric_semantics.yaml", manifest)
        self.assertTrue((ROOT / "provider" / "metric_semantics.yaml").is_file())
        self.assertTrue((ROOT / "main.py").is_file())

    def test_every_tool_has_yaml_and_python_source(self) -> None:
        provider = (ROOT / "provider" / "metric_semantics.yaml").read_text(encoding="utf-8")
        for name in TOOL_NAMES:
            self.assertIn(f"tools/{name}.yaml", provider)
            yaml_path = ROOT / "tools" / f"{name}.yaml"
            self.assertTrue(yaml_path.is_file(), yaml_path)
            body = yaml_path.read_text(encoding="utf-8")
            self.assertIn(f"name: {name}", body)
            self.assertIn(f"source: tools/{name}.py", body)
            self.assertTrue((ROOT / "tools" / f"{name}.py").is_file())

    def test_required_tool_parameters(self) -> None:
        resolve = (ROOT / "tools" / "resolve_metric.yaml").read_text(encoding="utf-8")
        self.assertIn("name: query", resolve)
        self.assertIn("required: true", resolve)

        arbitrate = (ROOT / "tools" / "arbitrate.yaml").read_text(encoding="utf-8")
        for field in ("candidate_ids", "candidates_json", "confidence_threshold"):
            self.assertIn(f"name: {field}", arbitrate)

        record = (ROOT / "tools" / "record_outcome.yaml").read_text(encoding="utf-8")
        self.assertIn("name: metric_id", record)
        self.assertIn("name: outcome", record)

    def test_repository_hygiene_files(self) -> None:
        self.assertEqual((ROOT / ".gitattributes").read_text(encoding="utf-8").strip(), "* text=auto eol=lf")
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for pattern in ("__pycache__/", "*.py[cod]", "data/"):
            self.assertIn(pattern, gitignore)

        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("ubuntu-latest", ci)
        self.assertIn('"3.11"', ci)
        self.assertIn('"3.12"', ci)
        self.assertIn("python -m unittest discover -s tests -v", ci)

    def test_readme_states_the_honest_limitation(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Dify", readme)
        self.assertIn("未在本机验证", readme)


if __name__ == "__main__":
    unittest.main()