import datetime
from dapr.actor import Actor, Remindable
from backup_actor_interface import BackupActorInterface
from cosmosdb_helper import cosmosdb_query_items, cosmosdb_create_item
import asyncio
from common_types import BackupConfig, BackupStatus, BackupTaskStatus
from dataclasses import asdict
import uuid
import shutil
import os

class BackupActor(Actor, BackupActorInterface, Remindable):
    

    def __init__(self, ctx, actor_id):
        super(BackupActor, self).__init__(ctx, actor_id)
        self.backup_config : BackupConfig = None

    async def _on_activate(self) -> None:
        print(f'Activate {self.__class__.__name__} actor! Backup Config: {self.backup_config}', flush=True)
        has_value, data = await self._state_manager.try_get_state('backup_config')
        if has_value:
            self.backup_config = BackupConfig(**data)



    async def _on_deactivate(self) -> None:
        print(f'Deactivate {self.__class__.__name__} actor!', flush=True)

    async def init_backup(self, data: dict) -> None:
        # Implementation for starting a backup
        print(f'Starting backup for {data}', flush=True)
        backup_config = BackupConfig(**data)
        
        await self._state_manager.set_state('backup_config', asdict(backup_config))
        backup_status = BackupTaskStatus(
            user_id=backup_config.user_id,
            backup_task_id=backup_config.id,
            id=str(uuid.uuid4()),
            server_name=backup_config.server_name,
            file_path=backup_config.file_path,
            backup_path="",
            status=BackupStatus.SCHEDULED.value
        )
        await cosmosdb_create_item(asdict(backup_status))
        print(f'Backup initialized with config: {asdict(backup_config)}', flush=True)
        

    async def set_reminder(self, enabled: bool) -> None:
        has_value, data = await self._state_manager.try_get_state('backup_config')
        if has_value:
            self.backup_config = BackupConfig(**data)

        print(f'set reminder to {enabled}', flush=True)
        print(f'Backup Config in set_reminder: {self.backup_config}', flush=True)
        if enabled:
            # register (persisted) reminder
            await self.register_reminder(
                f'RetrieveTasksReminder_{self.id}',
                b'reminder_state',
                datetime.timedelta(seconds=self.backup_config.backup_frequency),  # first fire after 5s
                datetime.timedelta(seconds=self.backup_config.backup_frequency),  # then every 5s
            )
        else:
            # idempotent unregister
            try:
                await self.unregister_reminder(f'RetrieveTasksReminder_{self.id}')
            except Exception as e:
                print(f'unregister_reminder ignored: {e}', flush=True)
        print('set_reminder is done', flush=True)

    async def update_backup_status(self, status: str) -> None:
        # Implementation for updating the backup status
        ...
    

    async def receive_reminder(
        self,
        name: str,
        state: bytes,
        due_time: datetime.timedelta,
        period: datetime.timedelta,
        *args
    ) -> None:
        print(f"Received reminder: {name}, state: {state}, due_time: {due_time}, period: {period}", flush=True)
        await self.run_backup()


    async def run_backup(self):
        print(f"Running backup for {self.id} Backup Config: {self.backup_config}", flush=True)
        #await self.unregister_reminder(f'RetrieveTasksReminder_{self.id}')

        
        src_path = os.path.join(os.getcwd(), 'test_folder', self.backup_config.file_path)
        dest_path = os.path.join(os.getcwd(), 'backup_test_folder', self.backup_config.file_path + str(uuid.uuid4()))

        shutil.copy(src_path, dest_path)
        backup_status = BackupTaskStatus(
            user_id=self.backup_config.user_id,
            backup_task_id=self.backup_config.id,
            id=str(uuid.uuid4()),
            server_name=self.backup_config.server_name,
            file_path=src_path,
            backup_path=dest_path,
            status=BackupStatus.COMPLETED.value
        )
        await cosmosdb_create_item(asdict(backup_status))
        print(f"Actor Id {self.id} - Backup completed from {src_path} to {dest_path}", flush=True)
        await self.unregister_reminder(f'RetrieveTasksReminder_{self.id}')

