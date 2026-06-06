"""Integration tests for ``paper_bridge.summarizer.src.aws_helpers`` via moto.

Unlike ``tests/test_summarizer_aws_helpers.py`` (which injects hand mocks), these
tests run the *real* boto3 client paths against ``moto``'s in-memory AWS, then
assert on the resulting fake-AWS state. ``moto.mock_aws`` patches botocore at the
transport layer, so no request ever leaves the process — the dummy credentials
from the ``aws_credentials`` fixture guarantee we can never reach a real account.

Region is pinned to ``us-west-2`` everywhere for determinism.
"""

from __future__ import annotations

from pathlib import Path

import boto3
import pytest

moto = pytest.importorskip("moto")
from moto import mock_aws  # noqa: E402

from paper_bridge.summarizer.src.aws_helpers import (  # noqa: E402
    get_ssm_param_value,
    submit_batch_job,
    upload_dir_to_s3,
    upload_to_s3,
)

REGION = "us-west-2"
BUCKET = "paper-bridge-test-bucket"


def _make_bucket(session: boto3.Session) -> boto3.client:
    client = session.client("s3", region_name=REGION)
    client.create_bucket(
        Bucket=BUCKET,
        CreateBucketConfiguration={"LocationConstraint": REGION},
    )
    return client


@pytest.mark.integration
@mock_aws
class TestUploadToS3:
    def test_uploads_single_file_to_bucket(
        self, aws_credentials: None, tmp_path: Path
    ) -> None:
        session = boto3.Session(region_name=REGION)
        s3 = _make_bucket(session)

        file_path = tmp_path / "summary.md"
        file_path.write_text("# Summary\nbody")

        ok = upload_to_s3(session, file_path, BUCKET, s3_prefix="reports")
        assert ok is True

        obj = s3.get_object(Bucket=BUCKET, Key="reports/summary.md")
        assert obj["Body"].read().decode() == "# Summary\nbody"

    def test_no_prefix_puts_object_at_root(
        self, aws_credentials: None, tmp_path: Path
    ) -> None:
        session = boto3.Session(region_name=REGION)
        s3 = _make_bucket(session)

        file_path = tmp_path / "root.txt"
        file_path.write_text("x")

        assert upload_to_s3(session, file_path, BUCKET) is True
        keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET).get("Contents", [])]
        assert keys == ["root.txt"]

    def test_missing_file_returns_false_and_uploads_nothing(
        self, aws_credentials: None, tmp_path: Path
    ) -> None:
        session = boto3.Session(region_name=REGION)
        s3 = _make_bucket(session)

        ok = upload_to_s3(session, tmp_path / "nope.md", BUCKET)
        assert ok is False
        assert s3.list_objects_v2(Bucket=BUCKET).get("Contents") is None

    def test_empty_bucket_name_returns_false(
        self, aws_credentials: None, tmp_path: Path
    ) -> None:
        session = boto3.Session(region_name=REGION)
        file_path = tmp_path / "f.md"
        file_path.write_text("x")
        assert upload_to_s3(session, file_path, "") is False

    def test_missing_bucket_returns_false(
        self, aws_credentials: None, tmp_path: Path
    ) -> None:
        # boto3's high-level upload_file wraps the underlying NoSuchBucket error
        # in boto3.exceptions.S3UploadFailedError (NOT a ClientError). upload_to_s3
        # now catches both, so uploading to a nonexistent bucket returns False
        # uniformly rather than propagating. (Regression guard for the fix.)
        session = boto3.Session(region_name=REGION)
        file_path = tmp_path / "f.md"
        file_path.write_text("x")
        assert upload_to_s3(session, file_path, "no-such-bucket-xyz") is False


@pytest.mark.integration
@mock_aws
class TestUploadDirToS3:
    def test_uploads_all_files_preserving_structure(
        self, aws_credentials: None, tmp_path: Path
    ) -> None:
        session = boto3.Session(region_name=REGION)
        s3 = _make_bucket(session)

        (tmp_path / "a.md").write_text("a")
        nested = tmp_path / "sub"
        nested.mkdir()
        (nested / "b.md").write_text("b")

        count = upload_dir_to_s3(session, str(tmp_path), BUCKET, prefix="out")
        assert count == 2

        keys = sorted(o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET)["Contents"])
        assert keys == ["out/a.md", "out/sub/b.md"]

    def test_extension_filter_only_uploads_matching(
        self, aws_credentials: None, tmp_path: Path
    ) -> None:
        session = boto3.Session(region_name=REGION)
        s3 = _make_bucket(session)

        (tmp_path / "keep.md").write_text("k")
        (tmp_path / "drop.txt").write_text("d")

        count = upload_dir_to_s3(
            session, str(tmp_path), BUCKET, prefix="p", file_ext_to_incl=["md"]
        )
        assert count == 1
        keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET)["Contents"]]
        assert keys == ["p/keep.md"]

    def test_error_returns_zero_when_bucket_missing(
        self, aws_credentials: None, tmp_path: Path
    ) -> None:
        session = boto3.Session(region_name=REGION)
        (tmp_path / "a.md").write_text("a")
        # No bucket created -> upload raises, function returns 0 (swallowed).
        assert upload_dir_to_s3(session, str(tmp_path), "missing-bkt", prefix="p") == 0


@pytest.mark.integration
@mock_aws
class TestGetSsmParamValueIntegration:
    def test_returns_stored_value(self, aws_credentials: None) -> None:
        session = boto3.Session(region_name=REGION)
        ssm = session.client("ssm", region_name=REGION)
        ssm.put_parameter(
            Name="/paper-bridge/endpoint", Value="https://x", Type="String"
        )

        assert get_ssm_param_value(session, "/paper-bridge/endpoint") == "https://x"

    def test_secure_string_is_decrypted(self, aws_credentials: None) -> None:
        session = boto3.Session(region_name=REGION)
        ssm = session.client("ssm", region_name=REGION)
        ssm.put_parameter(Name="/secret", Value="s3cr3t", Type="SecureString")

        # Function passes WithDecryption=True, so the plain value comes back.
        assert get_ssm_param_value(session, "/secret") == "s3cr3t"

    def test_missing_parameter_returns_none(self, aws_credentials: None) -> None:
        session = boto3.Session(region_name=REGION)
        assert get_ssm_param_value(session, "/does/not/exist") is None


@pytest.mark.integration
@mock_aws
class TestSubmitBatchJobIntegration:
    def _provision_queue_and_def(self, session: boto3.Session) -> None:
        ec2 = session.client("ec2", region_name=REGION)
        vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
        subnet = ec2.create_subnet(VpcId=vpc, CidrBlock="10.0.0.0/24")["Subnet"][
            "SubnetId"
        ]
        sg = ec2.create_security_group(GroupName="test-sg", Description="d", VpcId=vpc)[
            "GroupId"
        ]

        iam = session.client("iam", region_name=REGION)
        iam.create_role(RoleName="instance-role", AssumeRolePolicyDocument="{}")
        prof = iam.create_instance_profile(InstanceProfileName="instance-profile")[
            "InstanceProfile"
        ]["Arn"]
        iam.add_role_to_instance_profile(
            InstanceProfileName="instance-profile", RoleName="instance-role"
        )
        svc_role = iam.create_role(
            RoleName="service-role", AssumeRolePolicyDocument="{}"
        )["Role"]["Arn"]

        batch = session.client("batch", region_name=REGION)
        ce = batch.create_compute_environment(
            computeEnvironmentName="test-compute-env",
            type="MANAGED",
            state="ENABLED",
            computeResources={
                "type": "EC2",
                "minvCpus": 0,
                "maxvCpus": 2,
                "instanceTypes": ["t2.micro"],
                "subnets": [subnet],
                "securityGroupIds": [sg],
                "instanceRole": prof,
            },
            serviceRole=svc_role,
        )["computeEnvironmentArn"]
        batch.create_job_queue(
            jobQueueName="test-queue",
            state="ENABLED",
            priority=1,
            computeEnvironmentOrder=[{"order": 1, "computeEnvironment": ce}],
        )
        batch.register_job_definition(
            jobDefinitionName="test-def",
            type="container",
            containerProperties={"image": "busybox", "vcpus": 1, "memory": 128},
        )

    def test_submits_and_returns_job_id(self, aws_credentials: None) -> None:
        session = boto3.Session(region_name=REGION)
        self._provision_queue_and_def(session)

        job_id = submit_batch_job(
            session,
            "test-job",
            "test-queue",
            "test-def",
            parameters={"arxiv_id": "2503.23461"},
        )
        assert isinstance(job_id, str) and job_id

        # The job is now visible in moto's fake Batch state.
        batch = session.client("batch", region_name=REGION)
        jobs = batch.describe_jobs(jobs=[job_id])["jobs"]
        assert len(jobs) == 1
        assert jobs[0]["jobName"] == "test-job"

    def test_missing_definition_raises_clienterror(self, aws_credentials: None) -> None:
        from botocore.exceptions import ClientError

        session = boto3.Session(region_name=REGION)
        # No queue/definition provisioned -> SubmitJob fails; helper re-raises.
        with pytest.raises(ClientError):
            submit_batch_job(session, "j", "no-queue", "no-def")
