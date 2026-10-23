"""Pinned registry of downloadable models (data only; the download service consumes it)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelFile:
    path: str  # relative path inside the model dir, e.g. "1_Pooling/config.json"
    url: str
    sha256: str
    size: int  # bytes


@dataclass(frozen=True)
class ModelSpec:
    id: str  # dir name under settings.models_dir
    name: str
    purpose: str
    required: bool
    license_name: str
    license_url: str
    license_summary: str
    files: tuple[ModelFile, ...]  # final files in the model dir
    archive: ModelFile | None = None  # if set: download this zip, extract `files` from it

    @property
    def total_size(self) -> int:
        return self.archive.size if self.archive else sum(f.size for f in self.files)


EMBED_MODEL_ID = "embeddinggemma-2"
FACE_MODEL_ID = "buffalo_l"

_EMBED_FILES = (
    ModelFile(
        "1_Pooling/config.json",
        "https://huggingface.co/google/embeddinggemma-2/resolve/914f7f89142e33e77833254d9c9b90c3cef7303b/1_Pooling/config.json",
        "8759bdf7c77efc7df7723f64856a593c8943b71ee38baf2a88771fbaf78438f9",
        90,
    ),
    ModelFile(
        "2_Normalize/config.json",
        "https://huggingface.co/google/embeddinggemma-2/resolve/914f7f89142e33e77833254d9c9b90c3cef7303b/2_Normalize/config.json",
        "cdb09dfca347a56aa2d691744e38d5ad3c7cbc2834e7181272b9a15328b82524",
        97,
    ),
    ModelFile(
        "chat_template.jinja",
        "https://huggingface.co/google/embeddinggemma-2/resolve/914f7f89142e33e77833254d9c9b90c3cef7303b/chat_template.jinja",
        "4b852efc0b9960283e735363331e6f325b33bc74bdbaa076f595bc4e9b94d85e",
        1016,
    ),
    ModelFile(
        "config.json",
        "https://huggingface.co/google/embeddinggemma-2/resolve/914f7f89142e33e77833254d9c9b90c3cef7303b/config.json",
        "b8f1e9931b57fbc054acdb445c41765d55b0074c58d145fa82839941ad1b5bb3",
        4455,
    ),
    ModelFile(
        "config_sentence_transformers.json",
        "https://huggingface.co/google/embeddinggemma-2/resolve/914f7f89142e33e77833254d9c9b90c3cef7303b/config_sentence_transformers.json",
        "031e56a498d33c349ab489a21885bcfe25b4fcba841149dc99e1e90d4a7c28f5",
        1565,
    ),
    ModelFile(
        "model.safetensors",
        "https://huggingface.co/google/embeddinggemma-2/resolve/914f7f89142e33e77833254d9c9b90c3cef7303b/model.safetensors",
        "197a32965d4b1105faf060417baa899e193fb73cd401f42ec9295234d5553d79",
        1488915288,
    ),
    ModelFile(
        "modules.json",
        "https://huggingface.co/google/embeddinggemma-2/resolve/914f7f89142e33e77833254d9c9b90c3cef7303b/modules.json",
        "3d02572a0455b832de67fb8e63a54981bc7e8b46e337c95e917bd8122a533bfd",
        413,
    ),
    ModelFile(
        "preprocessor_config.json",
        "https://huggingface.co/google/embeddinggemma-2/resolve/914f7f89142e33e77833254d9c9b90c3cef7303b/preprocessor_config.json",
        "ea2ae257e901064abdd98dceb19f2b0da06af600bed15e0f99f5c85c37ee9d78",
        511,
    ),
    ModelFile(
        "processor_config.json",
        "https://huggingface.co/google/embeddinggemma-2/resolve/914f7f89142e33e77833254d9c9b90c3cef7303b/processor_config.json",
        "168f6a08522f3ce5dea596d94d003af2fd691742d4f41fe1f9d8cce76bfbf69c",
        1788,
    ),
    ModelFile(
        "sentence_bert_config.json",
        "https://huggingface.co/google/embeddinggemma-2/resolve/914f7f89142e33e77833254d9c9b90c3cef7303b/sentence_bert_config.json",
        "b1bcd9f2dce3ae863b359e87d0710b5dbc3314a59ecb4e2f97c7778fc8e4b228",
        747,
    ),
    ModelFile(
        "tokenizer.json",
        "https://huggingface.co/google/embeddinggemma-2/resolve/914f7f89142e33e77833254d9c9b90c3cef7303b/tokenizer.json",
        "4d777ef5bdc1aa36227abdfb77c3e49e7b9c892d16e1b6bda41c393504828be4",
        32170510,
    ),
    ModelFile(
        "tokenizer.model",
        "https://huggingface.co/google/embeddinggemma-2/resolve/914f7f89142e33e77833254d9c9b90c3cef7303b/tokenizer.model",
        "e594c8a90eb08d8bda498ff4747977dc827ae0c3c56b5c0d41a605a22d02ef03",
        4689013,
    ),
    ModelFile(
        "tokenizer_config.json",
        "https://huggingface.co/google/embeddinggemma-2/resolve/914f7f89142e33e77833254d9c9b90c3cef7303b/tokenizer_config.json",
        "17bd5d6e9364ca49a534e1502076593317c298d4a663623091ed45388f004874",
        1599,
    ),
)

_FACE_ZIP = "https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip"

REGISTRY: dict[str, ModelSpec] = {
    EMBED_MODEL_ID: ModelSpec(
        id=EMBED_MODEL_ID,
        name="EmbeddingGemma",
        purpose="Image embeddings for finding similar photos",
        required=True,
        license_name="Apache-2.0",
        license_url="https://www.apache.org/licenses/LICENSE-2.0",
        license_summary="Apache License 2.0 — free to use, modify and share with attribution.",
        files=_EMBED_FILES,
    ),
    FACE_MODEL_ID: ModelSpec(
        id=FACE_MODEL_ID,
        name="InsightFace buffalo_l",
        purpose="Face detection and recognition for Faces & People",
        required=False,
        license_name="InsightFace non-commercial license",
        license_url="https://github.com/deepinsight/insightface#license",
        license_summary="InsightFace pretrained models: non-commercial research use only.",
        files=(
            ModelFile(
                "det_10g.onnx",
                _FACE_ZIP,
                "5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91",
                16923827,
            ),
            ModelFile(
                "w600k_r50.onnx",
                _FACE_ZIP,
                "4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43",
                174383860,
            ),
        ),
        archive=ModelFile(
            "buffalo_l.zip",
            _FACE_ZIP,
            "80ffe37d8a5940d59a7384c201a2a38d4741f2f3c51eef46ebb28218a7b0ca2f",
            288621354,
        ),
    ),
}


def get_spec(model_id: str) -> ModelSpec:
    return REGISTRY[model_id]
