"""Tests for injectable boto3 helpers in ``paper_bridge.summarizer.src.aws_helpers``.

These functions accept a ``boto3.Session`` argument, so we inject a ``MagicMock``
session whose ``.client(...)`` returns a controlled stub. No real AWS, no moto:
the clients are pure mocks, keeping the tests dependency-free and deterministic.
"""

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from paper_bridge.summarizer.src.aws_helpers import (
    get_cross_inference_model_id,
    get_ssm_param_value,
)


def _session_with_client(client: MagicMock) -> MagicMock:
    session = MagicMock()
    session.client.return_value = client
    return session


@pytest.mark.unit
class TestGetCrossInferenceModelId:
    def test_returns_prefixed_when_profile_exists(self) -> None:
        bedrock = MagicMock()
        bedrock.list_inference_profiles.return_value = {
            "inferenceProfileSummaries": [
                {"inferenceProfileId": "us.anthropic.claude-sonnet-4-6"}
            ]
        }
        session = _session_with_client(bedrock)
        result = get_cross_inference_model_id(
            session, "anthropic.claude-sonnet-4-6", "us-west-2"
        )
        assert result == "us.anthropic.claude-sonnet-4-6"

    def test_apac_prefix_for_ap_region(self) -> None:
        bedrock = MagicMock()
        bedrock.list_inference_profiles.return_value = {
            "inferenceProfileSummaries": [
                {"inferenceProfileId": "apac.anthropic.claude-sonnet-4-6"}
            ]
        }
        session = _session_with_client(bedrock)
        result = get_cross_inference_model_id(
            session, "anthropic.claude-sonnet-4-6", "ap-northeast-2"
        )
        assert result == "apac.anthropic.claude-sonnet-4-6"

    def test_falls_back_to_raw_when_not_in_profile_list(self) -> None:
        bedrock = MagicMock()
        bedrock.list_inference_profiles.return_value = {
            "inferenceProfileSummaries": [{"inferenceProfileId": "eu.some.other.model"}]
        }
        session = _session_with_client(bedrock)
        result = get_cross_inference_model_id(
            session, "anthropic.claude-sonnet-4-6", "us-west-2"
        )
        assert result == "anthropic.claude-sonnet-4-6"

    def test_falls_back_on_exception(self) -> None:
        bedrock = MagicMock()
        bedrock.list_inference_profiles.side_effect = RuntimeError("boom")
        session = _session_with_client(bedrock)
        result = get_cross_inference_model_id(
            session, "anthropic.claude-sonnet-4-6", "us-west-2"
        )
        assert result == "anthropic.claude-sonnet-4-6"

    @pytest.mark.parametrize(
        "session,model_id,region",
        [
            (None, "m", "us-west-2"),
            (MagicMock(), "", "us-west-2"),
            (MagicMock(), "m", ""),
        ],
    )
    def test_missing_params_raise_value_error(self, session, model_id, region) -> None:
        with pytest.raises(ValueError):
            get_cross_inference_model_id(session, model_id, region)


@pytest.mark.unit
class TestGetSsmParamValue:
    def test_returns_value(self) -> None:
        ssm = MagicMock()
        ssm.get_parameter.return_value = {"Parameter": {"Value": "the-endpoint"}}
        session = _session_with_client(ssm)
        assert get_ssm_param_value(session, "/some/param") == "the-endpoint"
        ssm.get_parameter.assert_called_once_with(
            Name="/some/param", WithDecryption=True
        )

    def test_client_error_returns_none(self) -> None:
        ssm = MagicMock()
        ssm.get_parameter.side_effect = ClientError(
            {"Error": {"Code": "ParameterNotFound", "Message": "nope"}},
            "GetParameter",
        )
        session = _session_with_client(ssm)
        assert get_ssm_param_value(session, "/missing") is None

    def test_empty_name_raises(self) -> None:
        with pytest.raises(ValueError):
            get_ssm_param_value(_session_with_client(MagicMock()), "")
