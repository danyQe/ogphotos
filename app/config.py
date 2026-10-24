from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.models_registry import EMBED_MODEL_ID, REGISTRY


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OGPHOTOS_")

    data_dir: Path = Path("./data")
    host: str = "127.0.0.1"
    port: int = 8000
    port_range_end: int = 8010  # launcher tries port..port_range_end
    embed_model: str = "google/embeddinggemma-2"
    embed_batch_size: int = 16
    # Used once the model is on cuda/mps; the embedder halves it on out-of-memory
    embed_batch_size_gpu: int = 32
    embed_dim: int = 512  # Matryoshka truncation; 512 is near-lossless for images, never 128
    device: str = "auto"  # auto | cuda | mps | cpu
    # Vision soft tokens per image (70–1120). 140 ranks like the default 280 (r=0.994 on
    # real photos) at less than half the CPU time.
    embed_image_tokens: int = 140
    # Runtime is offline: weights come from /setup/models ({models_dir}/embeddinggemma-2) or,
    # for development, the HF cache (scripts/embed_smoke.py).
    embed_local_files_only: bool = True
    # Photos are decoded once for analysis at this long edge (the model resizes anyway)
    analysis_max_side: int = 1024
    sharpness_max_side: int = 1024
    # Threads decoding photos ahead of the model (decode, not inference, bounds a GPU)
    decode_workers: int = 4

    # Similarity thresholds (defaults; user-adjustable copies live in the settings table)
    phash_max_distance: int = 8
    embed_min_cosine: float = 0.92
    similar_max_matches: int = 5  # match ids returned per similar_exists upload
    # Best-photo score weights (TECH_SPEC §7)
    best_weight_sharpness: float = 0.5
    best_weight_resolution: float = 0.3
    best_weight_size: float = 0.2
    # Neighbours considered per file when grouping
    group_max_neighbors: int = 200
    thumb_size: int = 400
    # Largest image accepted (~1.2 GB RAM to decode at 400 MP). Pillow's own limit is ~179 MP.
    max_image_pixels: int = 400_000_000
    upload_batch_size: int = 20
    upload_batch_max_bytes: int = 200 * 1024 * 1024  # big videos go in smaller batches

    # Faces (TECH_SPEC §13)
    face_enabled: bool = True  # default of the on/off switch on the Settings page
    face_batch_size: int = 8
    face_det_max_side: int = 1280  # photo decoded at this long edge for detection
    face_det_size: int = 640  # square detector input
    face_crop_size: int = 160
    face_neighbors: int = 50  # neighbours that vote when placing a face
    face_min_score: float = 0.6
    face_min_px: int = 60  # short side of a face in original pixels
    face_match_cosine: float = 0.45  # ArcFace same-person threshold; tune on real photos
    face_min_cluster: int = 3  # similar unassigned faces needed to create a person
    face_model_dir: Path | None = None  # default: {data_dir}/models/buffalo_l

    @property
    def library_dir(self) -> Path:
        return self.data_dir / "library"

    @property
    def thumbs_dir(self) -> Path:
        return self.data_dir / "thumbs"

    @property
    def trash_dir(self) -> Path:
        return self.data_dir / "trash"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def faces_dir(self) -> Path:
        return self.data_dir / "faces"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def face_model_path(self) -> Path:
        return self.face_model_dir or self.models_dir / "buffalo_l"

    @property
    def embed_model_source(self) -> str:
        """The installed local model dir if fully installed, else `embed_model` (HF cache, dev).

        A half-downloaded folder never counts: every registry file must be at full size."""
        local = self.models_dir / EMBED_MODEL_ID
        complete = all(
            (local / f.path).is_file() and (local / f.path).stat().st_size == f.size
            for f in REGISTRY[EMBED_MODEL_ID].files
        )
        return str(local) if complete else self.embed_model

    @property
    def db_path(self) -> Path:
        return self.data_dir / "ogphotos.db"

    def best_weights(self) -> dict[str, float]:
        return {
            "sharpness": self.best_weight_sharpness,
            "resolution": self.best_weight_resolution,
            "size": self.best_weight_size,
        }

    def tunable_defaults(self) -> dict[str, str]:
        """Defaults seeded into the settings table on first startup."""
        return {
            "phash_max_distance": str(self.phash_max_distance),
            "embed_min_cosine": str(self.embed_min_cosine),
            "embed_dim": str(self.embed_dim),
            "face_min_score": str(self.face_min_score),
            "face_min_px": str(self.face_min_px),
            "face_match_cosine": str(self.face_match_cosine),
            "face_min_cluster": str(self.face_min_cluster),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
