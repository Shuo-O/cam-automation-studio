from typing import Any


class PlanarMillingBuilder:
    Tolerance: float

    def Commit(self) -> object: ...
    def Destroy(self) -> None: ...


class CAMOperationCollection:
    def CreatePlanarMillingBuilder(
        self, operation: object | None
    ) -> PlanarMillingBuilder: ...


class CAMSetup:
    CAMOperationCollection: CAMOperationCollection
    def GenerateToolPath(self, operations: list[object]) -> None: ...
