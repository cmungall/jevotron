"""Select original assistant-message fields; no trace normalization."""

from pathlib import Path

from jevotron import Chunk, Config
from jevotron.parsers import JSONL


def traces(path):
    for chunk in JSONL(id_column="total_index")(path):
        # Never send human labels, reference answers, or tool-quality metadata.
        data = {
            key: value
            for key, value in chunk.data.items()
            if key
            in {"question", "task_description", "messages", "tools", "answer_text"}
        }
        fields = [
            f"/messages/{i}"
            for i, message in enumerate(data["messages"])
            if message["role"] == "assistant"
        ]
        yield Chunk(
            id=chunk.id,
            data=data,
            fields=[*fields, "/answer_text"],
            source=chunk.source,
        )


config = Config(
    parser=traces,
    labels=["POSITIVE", "NEUTRAL", "NEGATIVE"],
    criteria={
        "POSITIVE": "Correct and materially advances the task; for the final result, correct and complete.",
        "NEUTRAL": "Reasonable exploration with little progress, or insufficient evidence to judge.",
        "NEGATIVE": "Incorrect, counterproductive, or violates task constraints; for the final result, wrong or incomplete.",
    },
    anomaly_label="NEGATIVE",
    guidance=Path(__file__).with_name("guidance.md").read_text(),
)
