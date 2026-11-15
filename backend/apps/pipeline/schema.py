"""What the AI is asked to return for a letter (phase 1b subset of spec section 10).

The field names in the JSON are Dutch: that works better on Dutch letters. They
are aliases; the code uses the English names, and validation turns the values
into the English codes of the models.
"""

from typing import cast

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SenderOut(_Model):
    name: str | None = Field(alias="naam")
    quote: str | None = Field(alias="bron")
    confidence: float = Field(alias="zekerheid")


class SourcedDate(_Model):
    # YYYY-MM-DD
    value: str | None = Field(alias="waarde")
    quote: str | None = Field(alias="bron")
    confidence: float = Field(alias="zekerheid")


class ActionOut(_Model):
    type: str
    description: str = Field(alias="omschrijving")
    deadline: SourcedDate


class InvoiceOut(_Model):
    number: str | None = Field(alias="nummer")
    due_date: str | None = Field(alias="vervaldatum")
    amount_incl: str | None = Field(alias="bedrag_incl")
    quote: str | None = Field(alias="bron")
    confidence: float = Field(alias="zekerheid")


class ExtractionPayload(_Model):
    sender: SenderOut = Field(alias="afzender")
    document_type: str = Field(alias="documenttype")
    letter_date: SourcedDate = Field(alias="dagtekening")
    summary: str = Field(alias="samenvatting_b1")
    actions: list[ActionOut] = Field(alias="acties")
    invoice: InvoiceOut | None = Field(alias="factuur")


def json_schema() -> dict[str, object]:
    """The JSON schema sent as `response_format`, by alias and without references.

    Strict structured output (Mistral, Ollama) wants every object closed, every
    property required and no `$ref`.
    """
    schema = ExtractionPayload.model_json_schema(by_alias=True)
    definitions = cast(dict[str, object], schema.pop("$defs", {}))
    return cast(dict[str, object], _inline(schema, definitions))


def _inline(node: object, definitions: dict[str, object]) -> object:
    if isinstance(node, list):
        return [_inline(item, definitions) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        name = str(node["$ref"]).rsplit("/", 1)[-1]
        return _inline(definitions[name], definitions)
    result = {key: _inline(value, definitions) for key, value in node.items() if key != "title"}
    if result.get("type") == "object":
        result["additionalProperties"] = False
        result["required"] = list(cast(dict[str, object], result["properties"]))
    return result
