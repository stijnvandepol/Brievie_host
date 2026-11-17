import { t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createRoute } from "@tanstack/react-router";
import { useId, useState } from "react";
import {
  Button,
  Dialog,
  FieldError,
  Form,
  Heading,
  Input,
  Label,
  Modal,
  ModalOverlay,
  Radio,
  RadioGroup,
  Text,
  TextField,
} from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { aiStatusQuery, aiSuppliersQuery } from "../api/queries";
import type { components } from "../api/schema";
import { AiStatusLine } from "../components/ai-status-line";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import { Loading, MessageBar } from "../components/ui";
import { workspaceRoute } from "./workspace";

type AiStatus = components["schemas"]["AiStatusOut"];
type Supplier = components["schemas"]["AiSupplierOut"];
type Preview = components["schemas"]["AiSettingsPreviewOut"];
type Settings = components["schemas"]["AiSettingsIn"];

const MODES = ["off", "local", "external"] as const;
type Mode = (typeof MODES)[number];

function isMode(value: string): value is Mode {
  return (MODES as readonly string[]).includes(value);
}

/**
 * The notice of the server and the explicit yes. The text is the server's own, the same
 * the terminal command prints, so it is shown as it comes: line by line.
 */
function ConsentDialog({
  preview,
  isPending,
  error,
  onAgree,
  onClose,
}: {
  preview: Preview;
  isPending: boolean;
  error: unknown;
  onAgree: () => void;
  onClose: () => void;
}) {
  const [agreed, setAgreed] = useState(false);
  const checkId = useId();
  return (
    <Dialog role="alertdialog" className="dialog">
      <Heading slot="title" className="dialog-title">
        {t`AI aanzetten?`}
      </Heading>
      <p className="consent-notice">{preview.notice}</p>
      <div className="check">
        <input
          id={checkId}
          type="checkbox"
          checked={agreed}
          onChange={(event) => setAgreed(event.target.checked)}
        />
        <label htmlFor={checkId}>
          {t`Ik heb dit gelezen en ga akkoord, ook voor iedereen van wie post in deze werkruimte staat.`}
        </label>
      </div>
      <ErrorAlert error={error} />
      <div className="dialog-actions">
        <Button className="button secondary" onPress={onClose}>
          {t`Annuleren`}
        </Button>
        <Button className="button" isDisabled={!agreed} isPending={isPending} onPress={onAgree}>
          {t`AI aanzetten`}
        </Button>
      </div>
    </Dialog>
  );
}

function AiForm({
  workspaceId,
  status,
  suppliers,
}: {
  workspaceId: string;
  status: AiStatus;
  suppliers: Supplier[];
}) {
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<Mode>(isMode(status.mode) ? status.mode : "off");
  const known = suppliers.some((option) => option.key === status.supplier);
  const [supplier, setSupplier] = useState(
    known && status.supplier ? status.supplier : (suppliers[0]?.key ?? "mistral"),
  );
  const [apiKey, setApiKey] = useState("");
  // The server gives the model back but never the address: an own address is typed again.
  const [model, setModel] = useState(status.mode === "external" ? (status.model ?? "") : "");
  const [address, setAddress] = useState("");
  const [localAddress, setLocalAddress] = useState("");
  const [localModel, setLocalModel] = useState(status.mode === "local" ? (status.model ?? "") : "");
  const ownNow = status.mode === "external" && status.supplier === "custom";
  const [asked, setAsked] = useState<Preview | null>(null);
  const [saved, setSaved] = useState(false);

  const path = { workspace_id: workspaceId };
  const chosen = suppliers.find((option) => option.key === supplier);

  function settings(): Settings {
    if (mode === "external") {
      // An own address speaks the OpenAI protocol, whatever service is ticked above.
      const own = address.trim();
      return { mode, supplier: own ? "openai" : supplier, base_url: own, model: model.trim() };
    }
    if (mode === "local") {
      return { mode, supplier, base_url: localAddress.trim(), model: localModel.trim() };
    }
    return { mode, supplier, base_url: "", model: "" };
  }

  const save = useMutation({
    mutationFn: async (consentAddress: string | null) => {
      const body: Settings = { ...settings(), consent_address: consentAddress };
      // An empty field means: keep the key that is stored.
      if (mode === "external" && apiKey !== "") body.api_key = apiKey;
      const { error } = await api.PUT("/api/v1/workspaces/{workspace_id}/ai", {
        params: { path },
        body,
      });
      if (error !== undefined) throw new ApiFailure(error);
    },
    onSuccess: async () => {
      setAsked(null);
      setApiKey("");
      setSaved(true);
      await queryClient.invalidateQueries({ queryKey: aiStatusQuery(workspaceId).queryKey });
    },
  });

  /** Lets the server check the settings and say whether it wants a yes first. */
  const check = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/api/v1/workspaces/{workspace_id}/ai/preview", {
        params: { path },
        body: settings(),
      });
      if (error !== undefined || !data) throw new ApiFailure(error);
      return data;
    },
    onSuccess: (preview) => {
      if (preview.needs_consent) setAsked(preview);
      else save.mutate(null);
    },
  });

  const own = address.trim() !== "";
  // The server keeps a key only at the address it is already used for.
  const keepsKey =
    status.mode === "external" &&
    status.has_api_key &&
    (own ? ownNow : status.supplier === supplier);
  const modelRequired = own || chosen?.default_model === "";
  const modelField = (
    <TextField
      className="field"
      value={model}
      onChange={setModel}
      maxLength={100}
      isRequired={modelRequired}
    >
      <Label>{t`Model`}</Label>
      <Text slot="description">
        {modelRequired
          ? t`Vul de naam van het model in, zoals de dienst die noemt.`
          : t`Leeg laten kiest ${chosen?.default_model ?? ""}.`}
      </Text>
      <Input autoCapitalize="none" autoCorrect="off" spellCheck={false} />
      <FieldError />
    </TextField>
  );

  return (
    <>
      <Form
        className="form ai-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (check.isPending || save.isPending) return;
          setSaved(false);
          save.reset();
          check.mutate();
        }}
      >
        <RadioGroup
          className="choice"
          value={mode}
          onChange={(value) => {
            if (isMode(value)) setMode(value);
            setSaved(false);
          }}
        >
          <Label className="choice-label">{t`Wie leest je brieven?`}</Label>
          <div className="cards">
            <Radio value="off" className="card">
              <span className="card-top" aria-hidden="true">
                <span className="radio-indicator" />
              </span>
              <strong>{t`Geen AI`}</strong>
              <span>{t`Je vult zelf in wat er in een brief staat. Er gaat niets naar een AI.`}</span>
            </Radio>
            <Radio value="local" className="card">
              <span className="card-top" aria-hidden="true">
                <span className="radio-indicator" />
              </span>
              <strong>{t`AI op je eigen netwerk`}</strong>
              <span>{t`Een AI die je zelf thuis of op kantoor draait, zoals Ollama. Brievie stuurt de tekst alleen naar een adres op je eigen netwerk.`}</span>
            </Radio>
            <Radio value="external" className="card">
              <span className="card-top" aria-hidden="true">
                <span className="radio-indicator" />
              </span>
              <strong>{t`Een AI-dienst op internet`}</strong>
              <span>{t`De tekst van elke brief gaat naar een bedrijf dat de AI levert. Je krijgt eerst te zien wat er precies weggaat.`}</span>
            </Radio>
          </div>
        </RadioGroup>

        {mode === "external" ? (
          <>
            {ownNow ? (
              <MessageBar intent="info">
                {t`Deze werkruimte gebruikt nu een eigen adres (${status.host ?? ""}). Vul dat adres opnieuw in onder Geavanceerd als je het wilt houden.`}
              </MessageBar>
            ) : null}
            <RadioGroup
              className="choice"
              value={supplier}
              onChange={(value) => {
                setSupplier(value);
                // A model of one service means nothing at another.
                setModel(value === status.supplier ? (status.model ?? "") : "");
              }}
            >
              <Label className="choice-label">{t`Welke dienst?`}</Label>
              <div className="cards">
                {suppliers.map((option) => (
                  <Radio key={option.key} value={option.key} className="card">
                    <span className="card-top" aria-hidden="true">
                      <span className="radio-indicator" />
                    </span>
                    <strong>{option.name}</strong>
                    <span>{option.country}</span>
                  </Radio>
                ))}
              </div>
            </RadioGroup>
            <TextField
              className="field"
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={setApiKey}
              isRequired={!keepsKey}
              maxLength={500}
            >
              <Label>{t`Sleutel van de dienst (API-sleutel)`}</Label>
              <Text slot="description">
                {keepsKey
                  ? t`Er is al een sleutel opgeslagen. Laat dit leeg om die te houden.`
                  : t`Je maakt de sleutel aan in je account bij de dienst. Brievie bewaart hem versleuteld en laat hem nooit meer zien.`}
              </Text>
              <Input />
              <FieldError />
            </TextField>
            {modelRequired && !own ? modelField : null}
            <details className="advanced" open={ownNow || undefined}>
              <summary className="advanced-summary">{t`Geavanceerd`}</summary>
              {modelRequired && !own ? null : modelField}
              <TextField className="field" value={address} onChange={setAddress} maxLength={300}>
                <Label>{t`Eigen adres`}</Label>
                <Text slot="description">
                  {t`Alleen voor een andere dienst die werkt zoals OpenAI. Begint met https://. Vul je dit in, dan geldt de dienst die je hierboven koos niet meer en is een model verplicht.`}
                </Text>
                <Input type="url" autoCapitalize="none" autoCorrect="off" spellCheck={false} />
                <FieldError />
              </TextField>
            </details>
          </>
        ) : null}

        {mode === "local" ? (
          <>
            <TextField
              className="field"
              value={localAddress}
              onChange={setLocalAddress}
              isRequired
              maxLength={300}
            >
              <Label>{t`Adres van de AI op je netwerk`}</Label>
              <Text slot="description">{t`Bijvoorbeeld http://ollama:11434/v1`}</Text>
              <Input autoCapitalize="none" autoCorrect="off" spellCheck={false} />
              <FieldError />
            </TextField>
            <TextField
              className="field"
              value={localModel}
              onChange={setLocalModel}
              isRequired
              maxLength={100}
            >
              <Label>{t`Model`}</Label>
              <Input autoCapitalize="none" autoCorrect="off" spellCheck={false} />
              <FieldError />
            </TextField>
          </>
        ) : null}

        <ErrorAlert error={check.error ?? (asked ? null : save.error)} />
        {saved ? (
          <MessageBar intent="success" role="status">
            {t`Opgeslagen.`}
          </MessageBar>
        ) : null}
        <div className="form-actions">
          <Button
            type="submit"
            className="button"
            isPending={check.isPending || (save.isPending && !asked)}
          >
            {t`Opslaan`}
          </Button>
        </div>
      </Form>
      <ModalOverlay
        isOpen={asked !== null}
        onOpenChange={(open) => {
          if (!open) setAsked(null);
        }}
        className="overlay"
      >
        <Modal className="modal modal-wide">
          {asked ? (
            <ConsentDialog
              preview={asked}
              isPending={save.isPending}
              error={save.error}
              onAgree={() => save.mutate(asked.address)}
              onClose={() => {
                save.reset();
                setAsked(null);
              }}
            />
          ) : null}
        </Modal>
      </ModalOverlay>
    </>
  );
}

function AiPage() {
  usePageTitle(t`AI`);
  const { workspace } = aiRoute.useRouteContext();
  const status = useQuery(aiStatusQuery(workspace.id));
  const admin = workspace.role === "admin";
  const suppliers = useQuery({ ...aiSuppliersQuery(workspace.id), enabled: admin });
  return (
    <Shell workspace={workspace}>
      <PageHeader
        title={t`AI`}
        description={t`Een AI kan je brieven lezen en voorstellen wat erin staat. Hier zie je of dat aan staat en waar de tekst dan naartoe gaat.`}
      />
      <AiStatusLine workspaceId={workspace.id} />
      <ErrorAlert error={status.error ?? suppliers.error} />
      {admin ? (
        status.data && suppliers.data ? (
          <AiForm workspaceId={workspace.id} status={status.data} suppliers={suppliers.data} />
        ) : status.error || suppliers.error ? null : (
          <Loading />
        )
      ) : (
        <p className="muted">{t`Alleen de beheerder van deze werkruimte kan dit veranderen.`}</p>
      )}
    </Shell>
  );
}

export const aiRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "ai",
  component: AiPage,
});
