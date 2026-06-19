# CoreML type stubs — only what's used in hledac/brain/ane_embedder.py etc.
from typing import Any

class MLMultiArrayDataType:
    Int32: int
    Float32: int
    Float64: int
    Float16: int
    Double: int

MLMultiArrayDataTypeInt32: int
MLMultiArrayDataTypeFloat32: int
MLMultiArrayDataTypeDouble: int

class MLMultiArray:
    def __init__(self, shape: list[int], data_type: int = ..., error: Any = None) -> None: ...
    def count(self) -> int: ...
    def shape(self) -> list[int]: ...
    def data_type(self) -> int: ...
    def __getitem__(self, idx: int | tuple[int, ...]) -> Any: ...
    def __setitem__(self, idx: int | tuple[int, ...], val: Any) -> None: ...

class MLFeatureType:
    Int64: int
    Double: int
    String: int
    Image: int
    MultiArray: int
    Dictionary: int
    Sequence: int

class MLComputeUnit:
    CPUOnly: int
    CPUAndGPU: int
    All: int
    ANE_ONLY: int
    ANE: int

class MLModel:
    @classmethod
    def modelWithContentsOfURL_error_(cls, url: Any, error: Any) -> tuple[MLModel, Any]: ...  # noqa: N802
    @classmethod
    def modelWithConfiguration_error_(cls, config: MLModelConfiguration, error: Any) -> tuple[MLModel, Any]: ...  # noqa: N802
    def predictionFromFeatures_error_(self, features: Any, error: Any) -> tuple[Any, Any]: ...  # noqa: N802
    def predictionsFromBatch_error_(self, batch: Any, error: Any) -> tuple[Any, Any]: ...  # noqa: N802
    def modelDescription(self) -> MLModelDescription: ...  # noqa: N802
    def configuration(self) -> MLModelConfiguration: ...  # noqa: N802

class MLModelConfiguration:
    def __init__(self) -> None: ...
    def computeUnits(self) -> int: ...  # noqa: N802
    def setComputeUnits_(self, units: int) -> None: ...  # noqa: N802

class MLModelDescription:
    def inputDescriptionsByName(self) -> dict: ...  # noqa: N802
    def outputDescriptionsByName(self) -> dict: ...  # noqa: N802

class MLFeatureDescription:
    name: str
    type: int
    multiArrayConstraint: MLNumericConstraint | None

class MLFeatureValue:
    @classmethod
    def featureValueWithMultiArray_(cls, arr: MLMultiArray) -> MLFeatureValue: ...  # noqa: N802
    def multiArrayValue(self) -> MLMultiArray: ...  # noqa: N802
    def dictionaryValue(self) -> dict: ...  # noqa: N802

class MLDictionaryFeatureProvider:
    def __init__(self, dictionary: dict[str, MLFeatureValue]) -> None: ...
    def featureValueForName_(self, name: str) -> MLFeatureValue: ...  # noqa: N802
    def dictionary(self) -> dict: ...

class MLNumericConstraint:
    minNumber: float
    maxNumber: float
    enumeratedNumbers: list[float]

class MLImageSize:
    pixelsWide: int
    pixelsHigh: int

class MLImageSizeConstraint:
    pixelsWideRange: tuple[int, int]
    pixelsHighRange: tuple[int, int]

class MLModelAsset:
    @classmethod
    def modelAssetWithURL_error_(cls, url: Any, error: Any) -> tuple[MLModelAsset, Any]: ...  # noqa: N802

class MLFeatureProvider: ...
class MLArrayBatchProvider:
    def __init__(self, array: list[MLFeatureProvider]) -> None: ...
    def featuresAtIndex_(self, idx: int) -> MLFeatureProvider: ...  # noqa: N802
    def count(self) -> int: ...

class MLSendableFeatureValue:
    @classmethod
    def sendableFeatureValueWithMultiArray_(cls, arr: MLMultiArray) -> MLSendableFeatureValue: ...  # noqa: N802

class MLParameterKey:
    linkedModelFileName: str
    linkedModelSearchPath: str
    modelDisplayName: str
