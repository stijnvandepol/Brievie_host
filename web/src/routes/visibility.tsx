import { t } from "@lingui/core/macro";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createRoute, redirect } from "@tanstack/react-router";
import { useState } from "react";
import {
  Button,
  Dialog,
  Heading,
  Label,
  Modal,
  ModalOverlay,
  Radio,
  RadioGroup,
  Switch,
} from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import {
  membersQuery,
  visibilityQuery,
  workspaceSettingsQuery,
  workspacesQuery,
} from "../api/queries";
import type { components } from "../api/schema";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import { CheckRow, Loading } from "../components/ui";
import {
  type Confirmation,
  previewBody,
  useSightChange,
} from "../components/visibility-preview-dialog";
import { workspaceRoute } from "./workspace";

type Visibility = components["schemas"]["VisibilityOut"];
type Settings = components["schemas"]["WorkspaceSettingsOut"];
type Member = components["schemas"]["MemberOut"];
type Kind = "home" | "business";
type SwitchName = "everyone_sees_all" | "departments_enabled" | "admins_read_all";
type PresetField = SwitchName | "care_is_private" | "allow_member_export";
/** A change of the switches or the kind, as `updateVisibility` takes it (without the yes). */
type SettingsChange = Partial<
  Omit<components["schemas"]["VisibilityIn"], "confirmed" | "preview_token">
>;

/** The values a kind starts with (the server's `apps.workspaces.presets.PRESETS`). */
const PRESETS: Record<Kind, Record<PresetField | "emergency_access", boolean>> = {
  home: {
    everyone_sees_all: true,
    departments_enabled: false,
    admins_read_all: true,
    care_is_private: true,
    emergency_access: false,
    allow_member_export: true,
  },
  business: {
    everyone_sees_all: false,
    departments_enabled: true,
    admins_read_all: false,
    care_is_private: false,
    emergency_access: true,
    allow_member_export: false,
  },
};

const PRESET_FIELDS: PresetField[] = [
  "everyone_sees_all",
  "departments_enabled",
  "admins_read_all",
  "care_is_private",
  "allow_member_export",
];

/** A Thuis workspace says "Groepen" where an Organisatie says "Afdelingen" (R51). */
export function departmentsWord(kind: string): string {
  return kind === "home" ? t`Groepen gebruiken` : t`Afdelingen gebruiken`;
}

export function kindName(kind: string): string {
  return kind === "business" ? t`Organisatie` : t`Thuis`;
}

function fieldName(field: PresetField, kind: string): string {
  if (field === "everyone_sees_all") return t`Iedereen ziet alle post`;
  if (field === "departments_enabled") return departmentsWord(kind);
  if (field === "admins_read_all") return t`Beheerders lezen alle post`;
  if (field === "care_is_private") return t`Zorg-post is privé`;
  return t`Leden mogen hun eigen post exporteren`;
}

function onOff(value: boolean): string {
  return value ? t`aan` : t`uit`;
}

/** A list of names as a sentence: "Anna", "Anna en Bram", "Anna, Bram en Carla". */
export function namesSentence(names: string[]): string {
  if (names.length <= 1) return names[0] ?? "";
  const last = names[names.length - 1] ?? "";
  const rest = names.slice(0, -1).join(", ");
  return t`${rest} en ${last}`;
}

async function putVisibility(
  workspaceId: string,
  change: SettingsChange,
  confirmation: Confirmation,
): Promise<Visibility> {
  const { data, error } = await api.PUT("/api/v1/workspaces/{workspace_id}/visibility", {
    params: { path: { workspace_id: workspaceId } },
    body: { add_start_set: false, apply_preset: false, ...change, ...confirmation },
  });
  if (error !== undefined || !data) throw new ApiFailure(error);
  return data;
}

export async function putSortsPost(
  workspaceId: string,
  userId: string,
  sortsPost: boolean,
  confirmation: Confirmation,
) {
  const { data, error } = await api.PUT(
    "/api/v1/workspaces/{workspace_id}/members/{user_id}/sorts-post",
    {
      params: { path: { workspace_id: workspaceId, user_id: userId } },
      body: { sorts_post: sortsPost, ...confirmation },
    },
  );
  if (error !== undefined || !data) throw new ApiFailure(error);
  return data;
}

/** The dialog after choosing another Soort gebruik: one checkbox per value the kind
 * would change (all checked), emergency access as a line, and the start set. */
function PresetDialog({
  target,
  visibility,
  settings,
  onClose,
  onNext,
}: {
  target: Kind;
  visibility: Visibility;
  settings: Settings;
  onClose: () => void;
  onNext: (change: SettingsChange) => void;
}) {
  const current: Record<PresetField | "emergency_access", boolean> = {
    everyone_sees_all: visibility.everyone_sees_all,
    departments_enabled: visibility.departments_enabled,
    admins_read_all: visibility.admins_read_all,
    care_is_private: settings.care_is_private ?? false,
    allow_member_export: settings.allow_member_export,
    emergency_access: settings.emergency_access ?? false,
  };
  const preset = PRESETS[target];
  const differing = PRESET_FIELDS.filter((field) => preset[field] !== current[field]);
  const [taken, setTaken] = useState<Set<PresetField>>(() => new Set(differing));
  const [startSet, setStartSet] = useState(false);
  const title = t`Soort gebruik: ${kindName(target)}`;
  return (
    <ModalOverlay
      isOpen
      onOpenChange={(open) => !open && onClose()}
      isDismissable
      className="overlay"
    >
      <Modal className="modal">
        <Dialog className="dialog" aria-label={title}>
          <Heading slot="title" className="dialog-title">
            {title}
          </Heading>
          <p>{t`Brievie stelt deze instellingen voor. Zet een vinkje uit om iets te houden zoals het is.`}</p>
          <div className="checkbox-list">
            {differing.map((field) => (
              <CheckRow
                key={field}
                checked={taken.has(field)}
                onChange={(on) => {
                  const next = new Set(taken);
                  if (on) next.add(field);
                  else next.delete(field);
                  setTaken(next);
                }}
              >
                {`${fieldName(field, target)}: ${onOff(preset[field])}`}
              </CheckRow>
            ))}
          </div>
          {preset.emergency_access !== current.emergency_access ? (
            <p className="muted">{t`De serverbeheerder zet noodtoegang aan of uit.`}</p>
          ) : null}
          <CheckRow checked={startSet} onChange={setStartSet}>
            {t`Ook de startset labels toevoegen`}
          </CheckRow>
          <div className="dialog-actions">
            <Button className="button secondary" onPress={onClose}>
              {t`Annuleren`}
            </Button>
            <Button
              className="button"
              onPress={() => {
                const body: SettingsChange = {
                  kind: target,
                  apply_preset: true,
                  add_start_set: startSet,
                };
                // A value left unchecked keeps what it is: sent explicitly, because a value
                // the request leaves out takes the preset's.
                for (const field of differing) {
                  if (!taken.has(field)) body[field] = current[field];
                }
                onNext(body);
              }}
            >
              {t`Verder`}
            </Button>
          </div>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}

/** "Iemand toevoegen" to the sorters: one choice among members who do not sort yet. */
function AddSorterDialog({
  candidates,
  onClose,
  onChoose,
}: {
  candidates: Member[];
  onClose: () => void;
  onChoose: (member: Member) => void;
}) {
  const [chosen, setChosen] = useState<string>("");
  const title = t`Wie verdeelt post?`;
  return (
    <ModalOverlay
      isOpen
      onOpenChange={(open) => !open && onClose()}
      isDismissable
      className="overlay"
    >
      <Modal className="modal">
        <Dialog className="dialog" aria-label={title}>
          <Heading slot="title" className="dialog-title">
            {title}
          </Heading>
          {candidates.length === 0 ? (
            <p>{t`Er is niemand meer om toe te voegen. Iemand met de rol Alleen lezen kan geen post verdelen.`}</p>
          ) : (
            <RadioGroup className="choice" value={chosen} onChange={setChosen}>
              <Label className="choice-label">{t`Lid`}</Label>
              <div className="cards cards-stack">
                {candidates.map((member) => (
                  <Radio
                    key={member.user_id}
                    value={member.user_id}
                    className="card"
                    aria-label={member.name}
                  >
                    <span className="card-top" aria-hidden="true">
                      <strong>{member.name}</strong>
                      <span className="radio-indicator" />
                    </span>
                  </Radio>
                ))}
              </div>
            </RadioGroup>
          )}
          <div className="dialog-actions">
            <Button className="button secondary" onPress={onClose}>
              {t`Annuleren`}
            </Button>
            <Button
              className="button"
              isDisabled={!chosen}
              onPress={() => {
                const member = candidates.find((candidate) => candidate.user_id === chosen);
                if (member) onChoose(member);
              }}
            >
              {t`Toevoegen`}
            </Button>
          </div>
        </Dialog>
      </Modal>
    </ModalOverlay>
  );
}

function VisibilityPage() {
  usePageTitle(t`Wie ziet wat`);
  const { workspace } = visibilityRoute.useRouteContext();
  const queryClient = useQueryClient();
  const visibility = useQuery(visibilityQuery(workspace.id));
  const settings = useQuery(workspaceSettingsQuery(workspace.id));
  const memberList = useQuery(membersQuery(workspace.id));
  const sight = useSightChange(workspace.id);
  const [presetTarget, setPresetTarget] = useState<Kind | null>(null);
  const [addingSorter, setAddingSorter] = useState(false);
  const [error, setError] = useState<unknown>(undefined);
  const [busy, setBusy] = useState(false);

  async function refresh() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: visibilityQuery(workspace.id).queryKey }),
      queryClient.invalidateQueries({ queryKey: workspaceSettingsQuery(workspace.id).queryKey }),
      queryClient.invalidateQueries({ queryKey: membersQuery(workspace.id).queryKey }),
      queryClient.invalidateQueries({ queryKey: workspacesQuery.queryKey }),
    ]);
  }

  async function change(run: () => Promise<boolean>) {
    setError(undefined);
    setBusy(true);
    try {
      if (await run()) await refresh();
    } catch (reason) {
      setError(reason);
    } finally {
      setBusy(false);
    }
  }

  function changeSettings(body: SettingsChange, confirmLabel: string) {
    void change(() =>
      sight.previewFirst({
        preview: previewBody(body),
        confirmLabel,
        write: async (confirmation) => {
          const answer = await putVisibility(workspace.id, body, confirmation);
          queryClient.setQueryData(visibilityQuery(workspace.id).queryKey, answer);
        },
      }),
    );
  }

  function changeSorter(member: Member, sortsPost: boolean) {
    void change(() =>
      sight.tryFirst({
        preview: previewBody({
          member_change: "sorts_post",
          member_user_id: member.user_id,
          member_sorts_post: sortsPost,
        }),
        confirmLabel: t`Ja, ${member.name} verdeelt post`,
        write: (confirmation) =>
          putSortsPost(workspace.id, member.user_id, sortsPost, confirmation),
      }),
    );
  }

  const data = visibility.data;
  const allMembers = memberList.data ?? [];
  const admins = namesSentence(allMembers.filter((m) => m.role === "admin").map((m) => m.name));
  const sorterIds = new Set(data?.sorter_user_ids ?? []);
  const sorters = allMembers.filter((m) => sorterIds.has(m.user_id));
  const candidates = allMembers.filter((m) => m.role !== "viewer" && !sorterIds.has(m.user_id));
  const kind = data?.kind ?? "home";
  const sentences = data
    ? data.summary && data.summary.length > 0
      ? data.summary
      : data.sentences
    : [];

  function confirmLabelFor(name: SwitchName, on: boolean): string {
    if (!on) return t`Wijzigen`;
    if (name === "everyone_sees_all") return t`Ja, iedereen mag alle post zien`;
    if (name === "departments_enabled")
      return kind === "home" ? t`Ja, zet groepen aan` : t`Ja, zet afdelingen aan`;
    return t`Ja, beheerders lezen alle post`;
  }

  function sightSwitch(name: SwitchName, label: string) {
    const value = data?.[name] ?? false;
    return (
      <Switch
        className="switch"
        isSelected={value}
        isDisabled={busy}
        onChange={(on) => changeSettings({ [name]: on }, confirmLabelFor(name, on))}
      >
        <span className="switch-track" aria-hidden="true" />
        {label}
      </Switch>
    );
  }

  return (
    <Shell workspace={workspace}>
      <PageHeader
        title={t`Wie ziet wat`}
        breadcrumb={{
          to: "/w/$workspaceId/instellingen",
          params: { workspaceId: workspace.id },
          label: t`Instellingen`,
        }}
        description={t`Wie welke post ziet in ${workspace.name}. Elke wijziging laat eerst zien wie meer of minder gaat zien.`}
      />
      {visibility.isPending ? <Loading /> : null}
      <ErrorAlert error={visibility.error ?? error} />
      {data ? (
        <>
          <section className="panel panel-pad" aria-labelledby="visibility-now">
            <h2 id="visibility-now">{t`Zo is het nu`}</h2>
            <ul className="sentence-list">
              {sentences.map((sentence) => (
                <li key={sentence}>{sentence}</li>
              ))}
            </ul>
          </section>
          <section className="panel panel-pad" aria-labelledby="visibility-kind">
            <h2 id="visibility-kind">{t`Soort gebruik`}</h2>
            <RadioGroup
              className="choice form-narrow"
              aria-labelledby="visibility-kind"
              value={kind}
              isDisabled={busy || !settings.data}
              onChange={(next) => {
                if (next === "home" || next === "business") setPresetTarget(next);
              }}
            >
              <div className="cards cards-stack">
                {(["home", "business"] as const).map((option) => (
                  <Radio key={option} value={option} className="card" aria-label={kindName(option)}>
                    <span className="card-top" aria-hidden="true">
                      <strong>{kindName(option)}</strong>
                      <span className="radio-indicator" />
                    </span>
                  </Radio>
                ))}
              </div>
            </RadioGroup>
            <p className="switch-hint">{t`Dit bepaalt welke standaard Brievie voorstelt. Het verandert zelf niemands rechten.`}</p>
          </section>
          <section className="panel panel-pad" aria-labelledby="visibility-switches">
            <h2 id="visibility-switches">{t`Wie ziet welke post`}</h2>
            {sightSwitch("everyone_sees_all", t`Iedereen ziet alle post`)}
            <p className="switch-hint">{t`Aan: iedereen in Brievie ziet elke brief, behalve brieven die privé zijn. Uit: je ziet alleen post op je eigen naam en van je afdelingen.`}</p>
            {sightSwitch("departments_enabled", departmentsWord(kind))}
            <p className="switch-hint">
              {kind === "home"
                ? t`Zet post op naam van een groep, zoals Kinderen. Wie in die groep zit, ziet die post.`
                : t`Zet post op naam van een afdeling, zoals Finance. Wie in die afdeling zit, ziet die post.`}
            </p>
            {data.everyone_sees_all ? (
              <p className="switch-hint">
                {kind === "home"
                  ? t`Nu zien nog steeds alle mensen alle post; groepen helpen dan alleen bij het sorteren.`
                  : t`Nu zien nog steeds alle mensen alle post; afdelingen helpen dan alleen bij het sorteren.`}
              </p>
            ) : (
              <>
                {sightSwitch("admins_read_all", t`Beheerders lezen alle post`)}
                <p className="switch-hint">{t`Aan: ${admins} zien alle post die niet privé is. Uit: zij regelen wie wat ziet, maar lezen alleen post die voor hen is.`}</p>
              </>
            )}
          </section>
          {data.everyone_sees_all ? null : (
            <section className="panel panel-pad" aria-labelledby="visibility-unnamed">
              <h2 id="visibility-unnamed">{t`Post zonder naam`}</h2>
              <p>{t`Wie hier staat, ziet brieven waar nog geen naam of afdeling op staat, en zet er een naam op. Staat hier niemand, dan doen de beheerders dat: ${admins}.`}</p>
              {sorters.length > 0 ? (
                <ul className="plain-list">
                  {sorters.map((member) => (
                    <li key={member.user_id}>
                      <Switch
                        className="switch"
                        isSelected
                        isDisabled={busy}
                        aria-label={t`${member.name} verdeelt post`}
                        onChange={(on) => changeSorter(member, on)}
                      >
                        <span className="switch-track" aria-hidden="true" />
                        {member.name}: {t`Verdeelt post`}
                      </Switch>
                    </li>
                  ))}
                </ul>
              ) : null}
              <div>
                <Button
                  className="button secondary"
                  isDisabled={busy}
                  onPress={() => setAddingSorter(true)}
                >
                  {t`Iemand toevoegen`}
                </Button>
              </div>
            </section>
          )}
        </>
      ) : null}
      {presetTarget && data && settings.data ? (
        <PresetDialog
          target={presetTarget}
          visibility={data}
          settings={settings.data}
          onClose={() => setPresetTarget(null)}
          onNext={(body) => {
            setPresetTarget(null);
            changeSettings(body, t`Ja, gebruik ${kindName(presetTarget)}`);
          }}
        />
      ) : null}
      {addingSorter ? (
        <AddSorterDialog
          candidates={candidates}
          onClose={() => setAddingSorter(false)}
          onChoose={(member) => {
            setAddingSorter(false);
            changeSorter(member, true);
          }}
        />
      ) : null}
      {sight.dialog}
    </Shell>
  );
}

export const visibilityRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "instellingen/wie-ziet-wat",
  // For admins; the server refuses everyone else's changes too.
  beforeLoad: ({ context }) => {
    if (context.workspace.role !== "admin") {
      throw redirect({
        to: "/w/$workspaceId/inbox",
        params: { workspaceId: context.workspace.id },
      });
    }
  },
  component: VisibilityPage,
});
