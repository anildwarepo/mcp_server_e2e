from dapr.actor import ActorInterface, actormethod


class TaskManagerActorInterface(ActorInterface):

    @actormethod(name="GetTasks")
    async def get_tasks(self) -> list :
        ...

    @actormethod(name="RunTasks")
    async def run_tasks(self) -> None:
        ...

    @actormethod(name="SetReminder")
    async def set_reminder(self, enabled: bool) -> None:
        ...