class PipelineError(Exception):
    pass


class ClassificationError(PipelineError):
    pass


class ModuleError(PipelineError):
    def __init__(self, module_name: str, message: str):
        self.module_name = module_name
        super().__init__(f"[{module_name}] {message}")


class GoogleAuthError(PipelineError):
    pass
