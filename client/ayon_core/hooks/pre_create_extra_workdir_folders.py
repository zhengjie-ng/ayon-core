import os
from ayon_applications import PreLaunchHook, LaunchTypes
from ayon_core.pipeline.workfile import create_workdir_extra_folders


class CreateWorkdirExtraFolders(PreLaunchHook):
    """Create extra folders for the work directory.

    Based on setting `project_settings/global/tools/Workfiles/extra_folders`
    profile filtering will decide whether extra folders need to be created in
    the work directory.

    """

    # Execute after workfile template copy
    order = 15
    launch_types = {LaunchTypes.local}

    def execute(self):
        if not self.application.is_host:
            return

        env = self.data.get("env") or {}
        workdir = env.get("AYON_WORKDIR")
        if not workdir or not os.path.exists(workdir):
            return

        host_name = self.application.host_name
        task_type = self.data["task_type"]
        task_name = self.data["task_name"]
        project_name = self.data["project_name"]

        create_workdir_extra_folders(
            workdir, host_name, task_type, task_name, project_name,
        )
