import pyblish.api


class CollectHierarchy(pyblish.api.ContextPlugin):
    """Collecting hierarchy from `parents`.

    present in `clip` family instances coming from the request json data file

    It will add `hierarchical_context` into each instance for integrate
    plugins to be able to create needed parents for the context if they
    don't exist yet
    """

    label = "Collect Hierarchy"
    order = pyblish.api.CollectorOrder - 0.076
    families = ["shot"]
    hosts = ["resolve", "hiero", "flame"]

    def process(self, context):
        project_name = context.data["projectName"]
        final_context = {
            project_name: {
                "entity_type": "project",
                "children": {}
            },
        }
        temp_context = {}
        for instance in context:
            self.log.debug("Processing instance: `{}` ...".format(instance))

            # shot data dict
            product_type = instance.data["productType"]
            families = instance.data["families"]

            # exclude other families then self.families with intersection
            if not set(self.families).intersection(
                set(families + [product_type])
            ):
                continue

            # exclude if not masterLayer True
            if not instance.data.get("heroTrack"):
                continue

            shot_data = {
                "entity_type": "folder",
                # WARNING Default folder type is hardcoded
                # suppose that all instances are Shots
                "folder_type": "Shot",
                "tasks": instance.data.get("tasks") or {},
                "comments": instance.data.get("comments", []),
                "attributes": {
                    "handleStart": instance.data["handleStart"],
                    "handleEnd": instance.data["handleEnd"],
                    "frameStart": instance.data["frameStart"],
                    "frameEnd": instance.data["frameEnd"],
                    "clipIn": instance.data["clipIn"],
                    "clipOut": instance.data["clipOut"],
                    "fps": instance.data["fps"],
                    "resolutionWidth": instance.data["resolutionWidth"],
                    "resolutionHeight": instance.data["resolutionHeight"],
                    "pixelAspect": instance.data["pixelAspect"],
                },
            }
            # Split by '/' for AYON where asset is a path
            name = instance.data["folderPath"].split("/")[-1]
            actual = {name: shot_data}

            for parent in reversed(instance.data["parents"]):
                next_dict = {
                    parent["entity_name"]: {
                        "entity_type": "folder",
                        "folder_type": parent["folder_type"],
                        "children": actual,
                    }
                }
                actual = next_dict

            temp_context = self._update_dict(temp_context, actual)

        # skip if nothing for hierarchy available
        if not temp_context:
            return

        final_context[project_name]["children"] = temp_context

        # adding hierarchy context to context
        context.data["hierarchyContext"] = final_context
        self.log.debug("context.data[hierarchyContext] is: {}".format(
            context.data["hierarchyContext"]))

    def _update_dict(self, parent_dict, child_dict):
        """Nesting each child into its parent.

        Args:
            parent_dict (dict): parent dict wich should be nested with children
            child_dict (dict): children dict which should be injested
        """

        for key in parent_dict:
            if key in child_dict and isinstance(parent_dict[key], dict):
                child_dict[key] = self._update_dict(
                    parent_dict[key], child_dict[key]
                )
            else:
                if parent_dict.get(key) and child_dict.get(key):
                    continue
                else:
                    child_dict[key] = parent_dict[key]

        return child_dict
