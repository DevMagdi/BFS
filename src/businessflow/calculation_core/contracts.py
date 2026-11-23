from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from businessflow.calculation_core.decimal import (
    RoundingMode,
    parse_decimal_text,
    parse_output_decimal_text,
    validate_currency_code,
)
from businessflow.metrics.snapshot import content_hash

_DECIMAL_PATTERN = r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"
_HASH_PATTERN = r"^[0-9a-f]{64}$"
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$"
_VERSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,31}$"

DecimalText = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=64, pattern=_DECIMAL_PATTERN),
]
OutputDecimalText = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=512, pattern=_DECIMAL_PATTERN),
]
Identifier = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN),
]
VersionText = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=32, pattern=_VERSION_PATTERN),
]
HashText = Annotated[str, StringConstraints(strict=True, pattern=_HASH_PATTERN)]
NonEmptyText = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=512, pattern=r".*\S.*"),
]
CurrencyCode = Annotated[
    str,
    StringConstraints(strict=True, min_length=3, max_length=3, pattern=r"^[A-Z]{3}$"),
]
InputKind = Literal["observation", "assumption"]
FormulaOperation = Literal["add", "subtract", "multiply", "divide"]
RoundingTarget = Literal["none", "currency_minor_unit", "fixed_scale"]
ApprovalStatus = Literal["pending", "approved"]


class ContractModel(BaseModel):
    """Immutable, strict wire contracts; Decimal values are always decimal text."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SourceReference(ContractModel):
    schema_version: Literal[1] = 1
    source_id: Identifier
    source_hash: HashText
    record_hash: HashText | None = None
    dataset_type: Identifier | None = None
    mapping_version: Annotated[int, Field(strict=True, ge=1)] | None = None
    record_locator: NonEmptyText
    tenant_id: UUID
    company_id: UUID


class NumericObservation(ContractModel):
    schema_version: Literal[1] = 1
    key: Identifier
    value: DecimalText
    currency_code: CurrencyCode | None
    source: SourceReference

    @field_validator("value")
    @classmethod
    def value_must_be_decimal_text(cls, value: str) -> str:
        parse_decimal_text(value)
        return value

    @field_validator("currency_code")
    @classmethod
    def currency_must_be_supported(cls, value: str | None) -> str | None:
        if value is not None:
            validate_currency_code(value)
        return value


class NumericAssumption(ContractModel):
    schema_version: Literal[1] = 1
    key: Identifier
    version: VersionText
    value: DecimalText
    currency_code: CurrencyCode | None
    rationale: NonEmptyText
    attributed_by_user_id: UUID
    attributed_at: datetime
    source: SourceReference | None = None

    @field_validator("value")
    @classmethod
    def value_must_be_decimal_text(cls, value: str) -> str:
        parse_decimal_text(value)
        return value

    @field_validator("currency_code")
    @classmethod
    def currency_must_be_supported(cls, value: str | None) -> str | None:
        if value is not None:
            validate_currency_code(value)
        return value

    @field_validator("attributed_at")
    @classmethod
    def attribution_time_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("attributed_at must include a timezone")
        return value


class CalculationInputSnapshot(ContractModel):
    schema_version: Literal[1] = 1
    snapshot_id: UUID
    tenant_id: UUID
    company_id: UUID
    captured_at: datetime
    observations: tuple[NumericObservation, ...] = Field(default_factory=tuple)
    assumptions: tuple[NumericAssumption, ...] = Field(default_factory=tuple)

    @field_validator("captured_at")
    @classmethod
    def captured_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_keys_and_source_scope(self) -> CalculationInputSnapshot:
        keys = [item.key for item in self.observations]
        keys.extend(item.key for item in self.assumptions)
        if len(keys) != len(set(keys)):
            raise ValueError("observation and assumption keys must be unique")
        references = [item.source for item in self.observations]
        references.extend(item.source for item in self.assumptions if item.source is not None)
        if any(
            source.tenant_id != self.tenant_id or source.company_id != self.company_id
            for source in references
        ):
            raise ValueError("source references must match the input snapshot tenant and company")
        return self


class RoundingPolicy(ContractModel):
    target: RoundingTarget
    mode: RoundingMode
    scale: Annotated[int, Field(strict=True, ge=0, le=18)] | None = None

    @model_validator(mode="after")
    def validate_target_scale(self) -> RoundingPolicy:
        if self.target == "fixed_scale" and self.scale is None:
            raise ValueError("fixed_scale rounding requires an explicit scale")
        if self.target != "fixed_scale" and self.scale is not None:
            raise ValueError("scale is only valid for fixed_scale rounding")
        return self


class FormulaPolicyBinding(ContractModel):
    """A caller-supplied policy/hash assertion; it does not prove approver authority."""

    policy_id: Identifier
    policy_version: VersionText
    approval_status: ApprovalStatus
    approved_by_user_id: UUID | None = None
    approved_at: datetime | None = None
    approval_reference: NonEmptyText | None = None
    approved_formula_hash: HashText | None = None

    @field_validator("approved_at")
    @classmethod
    def approval_time_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("approved_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_approval_record(self) -> FormulaPolicyBinding:
        approval_fields = (
            self.approved_by_user_id,
            self.approved_at,
            self.approval_reference,
            self.approved_formula_hash,
        )
        if self.approval_status == "pending" and any(item is not None for item in approval_fields):
            raise ValueError("pending policy bindings cannot contain approval evidence")
        if self.approval_status == "approved" and any(item is None for item in approval_fields):
            raise ValueError("approved policy bindings require complete approval evidence")
        return self


class FormulaDefinition(ContractModel):
    schema_version: Literal[1] = 1
    formula_id: Identifier
    formula_version: VersionText
    operation: FormulaOperation
    left_input_key: Identifier
    left_input_kind: InputKind
    right_input_key: Identifier
    right_input_kind: InputKind
    output_key: Identifier
    output_label: NonEmptyText
    rounding_policy: RoundingPolicy
    policy_binding: FormulaPolicyBinding | None = None

    @model_validator(mode="after")
    def validate_policy_binding(self) -> FormulaDefinition:
        binding = self.policy_binding
        if (
            binding is not None
            and binding.approval_status == "approved"
            and binding.approved_formula_hash != formula_contract_hash(self)
        ):
            raise ValueError("policy approval must bind to this exact formula contract")
        return self


class MechanicalSumFormulaDefinition(ContractModel):
    """A generic exact-sum primitive, with no accounting interpretation or policy claim.

    Domain callers must provide their own versioned evidence/grouping contract. This type is
    intentionally limited to adding at most 10,000 observed values with one identical currency
    (including all-uncurrency values), no assumptions, no conversion and no rounding.
    """

    schema_version: Literal[1] = 1
    formula_id: Identifier
    formula_version: VersionText
    operation: Literal["exact_sum"] = "exact_sum"
    input_keys: tuple[Identifier, ...] = Field(min_length=1, max_length=10_000)
    output_key: Identifier
    output_label: NonEmptyText
    rounding_policy: RoundingPolicy
    interpretation: Literal["mechanical_exact_sum_only_not_accounting_policy"] = (
        "mechanical_exact_sum_only_not_accounting_policy"
    )

    @model_validator(mode="after")
    def validate_mechanical_sum_contract(self) -> MechanicalSumFormulaDefinition:
        if len(self.input_keys) != len(set(self.input_keys)):
            raise ValueError("mechanical exact-sum input keys must be unique")
        if (
            self.rounding_policy.target != "none"
            or self.rounding_policy.mode != "half_even"
            or self.rounding_policy.scale is not None
        ):
            raise ValueError("mechanical exact sums require the canonical no-rounding policy")
        return self


class CalculationOutput(ContractModel):
    schema_version: Literal[1] = 1
    key: Identifier
    label: NonEmptyText
    value: OutputDecimalText
    currency_code: CurrencyCode | None

    @field_validator("value")
    @classmethod
    def value_must_be_finite_decimal_text(cls, value: str) -> str:
        parse_output_decimal_text(value)
        return value

    @field_validator("currency_code")
    @classmethod
    def currency_must_be_supported(cls, value: str | None) -> str | None:
        if value is not None:
            validate_currency_code(value)
        return value


class MissingEvidence(ContractModel):
    code: Literal[
        "required_observation_missing",
        "required_assumption_missing",
        "input_kind_mismatch",
        "policy_approval_required",
    ]
    input_key: Identifier | None
    policy_id: Identifier | None
    message: NonEmptyText


class RoundingTrace(ContractModel):
    target: RoundingTarget
    mode: RoundingMode
    requested_scale: Annotated[int, Field(strict=True, ge=0, le=18)] | None
    currency_exponent: Annotated[int, Field(strict=True, ge=0)] | None
    calculation_precision: Annotated[int, Field(strict=True, gt=0)]
    rounding_precision: Annotated[int, Field(strict=True, gt=0)]
    division_inexact: bool
    quantization_performed: bool
    value_changed_by_rounding: bool
    unrounded_value: OutputDecimalText | None
    rounded_value: OutputDecimalText | None

    @field_validator("unrounded_value", "rounded_value")
    @classmethod
    def optional_values_must_be_decimal_text(cls, value: str | None) -> str | None:
        if value is not None:
            parse_output_decimal_text(value)
        return value


class CalculationTrace(ContractModel):
    schema_version: Literal[1] = 1
    engine_version: VersionText
    tenant_id: UUID
    company_id: UUID
    formula: FormulaDefinition | MechanicalSumFormulaDefinition
    formula_hash: HashText
    formula_contract_hash: HashText
    input_snapshot: CalculationInputSnapshot
    input_snapshot_hash: HashText
    state: Literal["calculated", "abstained"]
    missing_evidence: tuple[MissingEvidence, ...]
    rounding: RoundingTrace
    output: CalculationOutput | None
    trace_hash: HashText

    @model_validator(mode="after")
    def validate_scope_and_state(self) -> CalculationTrace:
        if (
            self.tenant_id != self.input_snapshot.tenant_id
            or self.company_id != self.input_snapshot.company_id
        ):
            raise ValueError("trace scope must match its input snapshot")
        if self.state == "calculated" and (self.output is None or self.missing_evidence):
            raise ValueError("calculated traces require an output and complete evidence")
        if self.state == "abstained" and (self.output is not None or not self.missing_evidence):
            raise ValueError("abstained traces require missing evidence and no output")
        return self


class IndependentExpectedResult(ContractModel):
    schema_version: Literal[1] = 1
    value: DecimalText
    currency_code: CurrencyCode | None
    source: SourceReference

    @field_validator("value")
    @classmethod
    def value_must_be_decimal_text(cls, value: str) -> str:
        parse_decimal_text(value)
        return value

    @field_validator("currency_code")
    @classmethod
    def currency_must_be_supported(cls, value: str | None) -> str | None:
        if value is not None:
            validate_currency_code(value)
        return value


FormulaContract = FormulaDefinition | MechanicalSumFormulaDefinition


def formula_payload(formula: FormulaContract) -> dict[str, object]:
    return formula.model_dump(mode="json")


def formula_hash(formula: FormulaContract) -> str:
    """Hash the exact versioned formula contract, including any approval record."""
    return content_hash(formula_payload(formula))


def formula_contract_hash(formula: FormulaContract) -> str:
    """Hash formula semantics and policy identity, excluding mutable approval metadata."""
    payload = formula_payload(formula)
    binding = payload.get("policy_binding")
    if isinstance(binding, dict):
        payload["policy_binding"] = {
            "policy_id": binding["policy_id"],
            "policy_version": binding["policy_version"],
        }
    return content_hash(payload)


def input_snapshot_payload(snapshot: CalculationInputSnapshot) -> dict[str, object]:
    """Return a canonical, order-independent JSON payload retaining all exact evidence."""
    payload = snapshot.model_dump(mode="json")
    payload["observations"] = sorted(payload["observations"], key=lambda item: item["key"])
    payload["assumptions"] = sorted(payload["assumptions"], key=lambda item: item["key"])
    return payload


def input_snapshot_hash(snapshot: CalculationInputSnapshot) -> str:
    return content_hash(input_snapshot_payload(snapshot))
