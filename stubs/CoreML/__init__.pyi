# Stub for CoreML (pyobjc-framework-CoreML; Darwin-only)
# Re-exports MLMultiArray, MLModel, MLDictionaryFeatureProvider from Foundation
# and declares CoreML-specific types.

from Foundation import NSURL, NSArray, NSDictionary, NSNumber  # type: ignore[attr-defined]

from ._types import (
    MLArrayBatchProvider,
    MLComputeUnit,
    MLDictionaryFeatureProvider,
    MLFeatureDescription,
    MLFeatureProvider,
    MLFeatureType,
    MLFeatureValue,
    MLImageSize,
    MLImageSizeConstraint,
    MLModel,
    MLModelAsset,
    MLModelConfiguration,
    MLModelDescription,
    MLMultiArray,
    MLMultiArrayDataType,
    MLMultiArrayDataTypeDouble,
    MLMultiArrayDataTypeFloat32,
    MLMultiArrayDataTypeInt32,
    MLNumericConstraint,
    MLParameterKey,
    MLPredictionOptions,
    MLSendableFeatureValue,
)

__all__ = [
    "MLMultiArray",
    "MLMultiArrayDataType",
    "MLMultiArrayDataTypeInt32",
    "MLMultiArrayDataTypeFloat32",
    "MLMultiArrayDataTypeDouble",
    "MLModel",
    "MLDictionaryFeatureProvider",
    "MLFeatureDescription",
    "MLFeatureValue",
    "MLFeatureType",
    "MLModelConfiguration",
    "MLComputeUnit",
    "MLModelDescription",
    "MLParameterKey",
    "MLPredictionOptions",
    "MLFeatureProvider",
    "MLArrayBatchProvider",
    "MLSendableFeatureValue",
    "MLNumericConstraint",
    "MLImageSize",
    "MLImageSizeConstraint",
    "MLModelAsset",
    "NSURL",
    "NSNumber",
    "NSArray",
    "NSDictionary",
]
