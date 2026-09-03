"""Environment validation for retained Validation Study results."""

from typing import cast

from scripts.validation_study.common import (
    ENVIRONMENT_KEYS,
    JsonObject,
    exact_object,
    git_commit_value,
    image_id_value,
    require,
    strict_string,
    utc_timestamp,
)
from trafficlab import __version__


def validate_environment(value: object) -> JsonObject:
    """Validate the exact runtime and source environment record."""
    document = exact_object(value, ENVIRONMENT_KEYS, name="environment")
    git_commit_value(document["git_commit"])
    for key in ("python_version", "trafficlab_version", "docker_engine_version", "docker_compose_version", "platform"):
        strict_string(document[key], name=f"environment {key}")
    image_id_value(document["target_image_id"], name="environment target image ID")
    image_id_value(document["capture_image_id"], name="environment capture image ID")
    utc_timestamp(document["study_date_utc"], name="environment study date")
    require(
        document["python_version"] == "3.12.3" and document["trafficlab_version"] == __version__,
        "environment Python and Trafficlab versions must equal the locked study versions",
    )
    return cast(JsonObject, document)
