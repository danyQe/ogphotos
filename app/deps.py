from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_session
from app.services.analysis import AnalysisWorker
from app.services.face_cluster import PeopleClusterer, ReclusterJob
from app.services.face_index import FaceIndex
from app.services.face_worker import FaceWorker
from app.services.importer import Importer
from app.services.people import People
from app.services.phash_index import PhashIndex
from app.services.regroup import Regrouper
from app.services.trash import Trash
from app.services.vector_index import VectorIndex


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_phash_index(request: Request) -> PhashIndex:
    return request.app.state.phash_index


def get_vector_index(request: Request) -> VectorIndex:
    return request.app.state.vector_index


def get_worker(request: Request) -> AnalysisWorker:
    return request.app.state.worker


def get_trash(request: Request) -> Trash:
    return request.app.state.trash


def get_importer(request: Request) -> Importer:
    return request.app.state.importer


def get_regrouper(request: Request) -> Regrouper:
    return request.app.state.regrouper


def get_face_worker(request: Request) -> FaceWorker:
    return request.app.state.face_worker


def get_recluster_job(request: Request) -> ReclusterJob:
    return request.app.state.recluster_job


def get_clusterer(request: Request) -> PeopleClusterer:
    return request.app.state.clusterer


def get_people(request: Request) -> People:
    return request.app.state.people


def get_face_index(request: Request) -> FaceIndex:
    return request.app.state.face_index


SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
PhashIndexDep = Annotated[PhashIndex, Depends(get_phash_index)]
VectorIndexDep = Annotated[VectorIndex, Depends(get_vector_index)]
WorkerDep = Annotated[AnalysisWorker, Depends(get_worker)]
TrashDep = Annotated[Trash, Depends(get_trash)]
ImporterDep = Annotated[Importer, Depends(get_importer)]
RegrouperDep = Annotated[Regrouper, Depends(get_regrouper)]
FaceWorkerDep = Annotated[FaceWorker, Depends(get_face_worker)]
ClustererDep = Annotated[PeopleClusterer, Depends(get_clusterer)]
ReclusterDep = Annotated[ReclusterJob, Depends(get_recluster_job)]
PeopleDep = Annotated[People, Depends(get_people)]
FaceIndexDep = Annotated[FaceIndex, Depends(get_face_index)]
