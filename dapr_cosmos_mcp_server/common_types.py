from dataclasses import dataclass
from enum import Enum

@dataclass
class BackupConfig:
    user_id: str
    id: str
    server_name: str
    file_path: str
    backup_frequency: str


@dataclass
class BackupStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SCHEDULED = "scheduled"


@dataclass
class BackupTaskStatus:
    user_id: str
    backup_task_id: str
    id: str
    server_name: str
    file_path: str
    backup_path: str
    status: BackupStatus