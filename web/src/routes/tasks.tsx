import { plural, t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createRoute, Link } from "@tanstack/react-router";
import { type ReactNode, useEffect, useState } from "react";
import {
  Button,
  Tab,
  TabList,
  TabPanel,
  Tabs,
  ToggleButton,
  ToggleButtonGroup,
} from "react-aria-components";
import { meQuery, myTasksQuery } from "../api/queries";
import { assignTask, myTasksKey, refreshAfterTask, tickTask } from "../api/tasks";
import { Icon } from "../components/icons";
import { ErrorAlert, PageHeader, Shell, usePageTitle } from "../components/page";
import { type TaskItem, TaskRow } from "../components/task-row";
import { EmptyState, Loading, MessageBar } from "../components/ui";
import { dateFormat } from "../format";
import { GROUPS, groupTitle, type TaskGroup } from "../task-time";
import { workspaceRoute } from "./workspace";

type Scope = "mine" | "all";

const shortDay = dateFormat({ day: "numeric", month: "short" });

/** Ticks a task off or opens it again; the open lists stay until the page is left. */
function useTick(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ taskId, done }: { taskId: string; done: boolean }) =>
      tickTask(workspaceId, taskId, done),
    onSettled: () => refreshAfterTask(queryClient, workspaceId, { keepLists: true }),
  });
}

function GroupHeading({ title, count }: { title: string; count: number }) {
  return (
    <h2 className="task-group-title">
      {title} <span className="task-group-count">({count})</span>
    </h2>
  );
}

function WaitingLine({ workspaceId, count }: { workspaceId: string; count: number }) {
  if (count <= 0) return null;
  return (
    <MessageBar intent="info">
      {plural(count, {
        one: "Er wacht nog # brief op nakijken. Daar kunnen nieuwe taken uit komen.",
        other: "Er wachten nog # brieven op nakijken. Daar kunnen nieuwe taken uit komen.",
      })}{" "}
      <Link to="/w/$workspaceId/inbox" params={{ workspaceId }}>
        {t`Naar Te doen`}
      </Link>
    </MessageBar>
  );
}

function OpenTasks({
  workspaceId,
  scope,
  split,
  canEdit,
}: {
  workspaceId: string;
  scope: Scope;
  split: boolean;
  canEdit: boolean;
}) {
  const queryClient = useQueryClient();
  const me = useQuery(meQuery).data;
  const list = useQuery(myTasksQuery(workspaceId, scope, "open"));
  const pickUp = useQuery({
    ...myTasksQuery(workspaceId, "to_pick_up", "open"),
    enabled: split && scope === "mine",
  });
  const tick = useTick(workspaceId);
  const take = useMutation({
    mutationFn: (taskId: string) => assignTask(workspaceId, taskId, me?.id ?? null),
    onSettled: () => refreshAfterTask(queryClient, workspaceId),
  });
  // Ticked here: shown struck through with "Ongedaan maken" until the page is left.
  const [ticked, setTicked] = useState<ReadonlySet<string>>(new Set());
  const [said, setSaid] = useState("");
  useEffect(
    () => () => {
      void queryClient.invalidateQueries({ queryKey: myTasksKey(workspaceId) });
    },
    [queryClient, workspaceId],
  );

  function onTick(task: TaskItem, done: boolean) {
    const description = task.description;
    tick.mutate(
      { taskId: task.id, done },
      {
        onSuccess: () => {
          setTicked((current) => {
            const next = new Set(current);
            if (done) next.add(task.id);
            else next.delete(task.id);
            return next;
          });
          setSaid(done ? t`Afgevinkt: ${description}` : t`Weer open: ${description}`);
        },
      },
    );
  }

  function row(task: TaskItem, extra?: ReactNode) {
    const isTicked = ticked.has(task.id);
    return (
      <TaskRow
        key={task.id}
        workspaceId={workspaceId}
        task={task}
        me={me?.name ?? ""}
        ticked={isTicked}
        canTick={canEdit && task.can_edit !== false && !tick.isPending}
        onTick={(done) => onTick(task, done)}
        actions={
          <>
            {isTicked && canEdit ? (
              <Button className="button-text" onPress={() => onTick(task, false)}>
                <Icon name="undo" />
                {t`Ongedaan maken`}
              </Button>
            ) : null}
            {extra}
          </>
        }
      />
    );
  }

  const data = list.data;
  const items = data?.items ?? [];
  const pickUpItems = pickUp.data?.items ?? [];
  return (
    <>
      <p className="visually-hidden" role="status" aria-live="polite">
        {said}
      </p>
      <ErrorAlert error={list.error ?? pickUp.error ?? tick.error ?? take.error} />
      {data && scope === "mine" ? (
        <WaitingLine workspaceId={workspaceId} count={data.waiting_letters} />
      ) : null}
      {data && scope === "mine" && !data.has_person ? (
        <MessageBar intent="info">
          {t`Je account hangt nog niet aan een naam. Daarom zie je hier alleen taken die iemand aan je geeft. Vraag een beheerder om je te koppelen bij Personen.`}
        </MessageBar>
      ) : null}
      {list.isPending ? <Loading /> : null}
      {data && items.length === 0 ? (
        <EmptyState icon="checklist">
          {t`Je hebt geen open taken. Nieuwe taken komen hier vanzelf zodra je post is nagekeken.`}
        </EmptyState>
      ) : null}
      {GROUPS.map((group: TaskGroup) => {
        const inGroup = items.filter((task) => task.group === group);
        if (inGroup.length === 0) return null;
        const title = groupTitle(group);
        return (
          <section key={group} className="task-group" aria-label={title}>
            <GroupHeading title={title} count={inGroup.length} />
            <ul className="task-rows">{inGroup.map((task) => row(task))}</ul>
          </section>
        );
      })}
      {data?.truncated ? (
        <p className="muted small">{t`Alleen de eerste 500 taken worden getoond.`}</p>
      ) : null}
      {split && scope === "mine" ? (
        <section className="task-group" aria-label={t`Op te pakken`}>
          <GroupHeading title={t`Op te pakken`} count={pickUpItems.length} />
          {pickUp.data && pickUpItems.length === 0 ? (
            <p className="muted">{t`Er ligt niets op te pakken.`}</p>
          ) : null}
          <ul className="task-rows">
            {pickUpItems.map((task) => {
              const description = task.description;
              return row(
                task,
                canEdit ? (
                  <Button
                    className="button-text"
                    aria-label={t`Ik doe dit: ${description}`}
                    isDisabled={take.isPending || !me}
                    onPress={() => take.mutate(task.id)}
                  >
                    {t`Ik doe dit`}
                  </Button>
                ) : null,
              );
            })}
          </ul>
        </section>
      ) : null}
    </>
  );
}

function DoneTasks({
  workspaceId,
  scope,
  canEdit,
}: {
  workspaceId: string;
  scope: Scope;
  canEdit: boolean;
}) {
  const queryClient = useQueryClient();
  const list = useQuery(myTasksQuery(workspaceId, scope, "done"));
  const reopen = useMutation({
    mutationFn: (taskId: string) => tickTask(workspaceId, taskId, false),
    onSettled: () => refreshAfterTask(queryClient, workspaceId),
  });
  const items = list.data?.items ?? [];
  return (
    <>
      <ErrorAlert error={list.error ?? reopen.error} />
      {list.isPending ? <Loading /> : null}
      {list.data && items.length === 0 ? (
        <EmptyState icon="checklist">{t`Je hebt de laatste 30 dagen niets afgevinkt.`}</EmptyState>
      ) : null}
      <ul className="task-rows">
        {items.map((task) => {
          const description = task.description;
          const who = task.completed_by_name ?? "";
          const when = task.completed_at ? shortDay.format(new Date(task.completed_at)) : "";
          return (
            <li key={task.id} className="task-row task-done">
              <div className="task-body">
                <p className="task-action">{description}</p>
                <p className="muted small">
                  {who ? t`Afgevinkt door ${who} op ${when}` : t`Afgevinkt op ${when}`}
                </p>
                <div className="task-actions">
                  {canEdit ? (
                    <Button
                      className="button-text"
                      aria-label={t`Toch niet klaar: ${description}`}
                      isDisabled={reopen.isPending}
                      onPress={() => reopen.mutate(task.id)}
                    >
                      <Icon name="undo" />
                      {t`Toch niet klaar`}
                    </Button>
                  ) : null}
                  {task.document_id ? (
                    <Link
                      to="/w/$workspaceId/post/$documentId"
                      params={{ workspaceId, documentId: task.document_id }}
                      aria-label={t`Brief bekijken: ${description}`}
                    >
                      {t`Brief bekijken`}
                    </Link>
                  ) : null}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </>
  );
}

/** "Mijn taken": what the member has to do with their post, by date (spec S 6, S 13). */
function TasksPage() {
  usePageTitle(t`Mijn taken`);
  const { workspace } = tasksRoute.useRouteContext();
  const [scope, setScope] = useState<Scope>("mine");
  const canEdit = workspace.role !== "viewer";
  const split = workspace.kind === "business";
  return (
    <Shell workspace={workspace}>
      <PageHeader
        title={t`Mijn taken`}
        description={
          split
            ? t`Taken die bij jou horen. Taken van je afdeling en post zonder naam staan bij Op te pakken.`
            : t`Wat je met je post moet doen, op datum. Je hoeft de brief er niet voor te openen.`
        }
      />
      {canEdit ? null : (
        <MessageBar intent="info">{t`Je kunt taken bekijken, maar niet afvinken.`}</MessageBar>
      )}
      <ToggleButtonGroup
        className="segmented"
        aria-label={t`Welke taken`}
        selectionMode="single"
        disallowEmptySelection
        selectedKeys={[scope]}
        onSelectionChange={(keys) => {
          const [next] = [...keys];
          if (next === "mine" || next === "all") setScope(next);
        }}
      >
        <ToggleButton id="mine" className="segment">
          {t`Mijn taken`}
        </ToggleButton>
        <ToggleButton id="all" className="segment">
          {t`Alle taken`}
        </ToggleButton>
      </ToggleButtonGroup>
      <Tabs className="tabs">
        <TabList aria-label={t`Taken`} className="tab-list">
          <Tab id="open" className="tab">
            {t`Open`}
          </Tab>
          <Tab id="done" className="tab">
            {t`Afgerond`}
          </Tab>
        </TabList>
        <TabPanel id="open">
          <OpenTasks workspaceId={workspace.id} scope={scope} split={split} canEdit={canEdit} />
        </TabPanel>
        <TabPanel id="done">
          <DoneTasks workspaceId={workspace.id} scope={scope} canEdit={canEdit} />
        </TabPanel>
      </Tabs>
    </Shell>
  );
}

export const tasksRoute = createRoute({
  getParentRoute: () => workspaceRoute,
  path: "taken",
  component: TasksPage,
});
