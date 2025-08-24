import datetime
from dapr.actor import Actor, Remindable
from dapr.actor import ActorProxy, ActorId 
from task_manager_actor_interface import TaskManagerActorInterface
from common_types import BackupConfig
from backup_actor_interface import BackupActorInterface
from cosmosdb_helper import cosmosdb_query_items, cosmosdb_create_item
import asyncio
import isodate
import uuid
from dataclasses import asdict

class TaskManagerActor(Actor, TaskManagerActorInterface, Remindable):
    def __init__(self, ctx, actor_id):
        super(TaskManagerActor, self).__init__(ctx, actor_id)

    async def _on_activate(self) -> None:
        print(f'Activate {self.__class__.__name__} actor!', flush=True)

    async def _on_deactivate(self) -> None:
        print(f'Deactivate {self.__class__.__name__} actor!', flush=True)

    async def set_reminder(self, enabled: bool) -> None:
        print(f'set reminder to {enabled}', flush=True)
        if enabled:
            # register (persisted) reminder
            await self.register_reminder(
                'RetrieveTasksReminder',
                b'reminder_state',
                datetime.timedelta(seconds=5),  # first fire after 5s
                datetime.timedelta(seconds=5),  # then every 5s
            )
        else:
            # idempotent unregister
            try:
                await self.unregister_reminder('RetrieveTasksReminder')
            except Exception as e:
                print(f'unregister_reminder ignored: {e}', flush=True)
        print('set_reminder is done', flush=True)


    async def receive_reminder(
        self,
        name: str,
        state: bytes,
        due_time: datetime.timedelta,
        period: datetime.timedelta,
        *args
    ) -> None:
        print(f"Received reminder: {name}, state: {state}, due_time: {due_time}, period: {period}", flush=True)
        await self.unregister_reminder('RetrieveTasksReminder')
        backup_items = await self.get_tasks()
        
        if backup_items:
            for item in backup_items:

                server_list = item.get('servers', [])
                for s in server_list:
                    file_list = item.get('files', [])
                    for f in file_list:
                        backup_id = ActorId(f"backup::{str(uuid.uuid4())}")
                        backup_proxy = ActorProxy.create('BackupActor', backup_id, BackupActorInterface)
                        backup_frequency_pth = item.get('backup_frequency_pth', 'daily')
                        duration = isodate.parse_duration(backup_frequency_pth)
                        seconds = duration.total_seconds()
                        print(f"Scheduling backup for file {f} on server {s} every {seconds} seconds", flush=True)
                        #  "backup_frequency_pth": "PT30S",
                        # convert p
                        
                        backup_config = BackupConfig(
                            user_id=item.get('user_id'),
                            id=item.get('id'),
                            server_name=s,
                            file_path=f,
                            backup_frequency=seconds
                        )
                        await backup_proxy.InitBackup(asdict(backup_config))
                        await backup_proxy.SetReminder(True)

    async def get_tasks(self) -> list:
        tasks = await cosmosdb_query_items()
        print(f"TaskManagerActor: {self.id} Retrieved tasks:", tasks)
        return tasks

    async def run_tasks(self) -> None:
        tasks = await self.get_tasks()
        for task in tasks:
            print("Running task:", task)
            # Simulate task processing
            await asyncio.sleep(1)
