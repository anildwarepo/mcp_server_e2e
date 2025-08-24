import datetime
from dapr.actor import ActorInterface, actormethod
from common_types import BackupConfig
from dataclasses import asdict
class BackupActorInterface(ActorInterface):

    @actormethod(name="InitBackup")
    async def init_backup(self, data: dict) -> None:
        ...

    @actormethod(name="SetReminder")
    async def set_reminder(self, enabled: bool) -> None:
        ...

    @actormethod(name="UpdateBackupStatus")
    async def update_backup_status(self, status: str) -> None:
        """
        Update the status of a backup operation.
        """
        ...