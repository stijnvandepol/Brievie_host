import { plural, t } from "@lingui/core/macro";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useId } from "react";
import { ApiFailure, api } from "../api/client";
import { devicesQuery, userDevicesQuery } from "../api/queries";
import type { components } from "../api/schema";
import { ConfirmAction } from "./confirm-dialog";
import { ErrorAlert } from "./page";
import { Badge, EmptyState, Loading } from "./ui";

type Device = components["schemas"]["DeviceOut"];

const dateTime = new Intl.DateTimeFormat("nl-NL", { dateStyle: "medium", timeStyle: "short" });

function DeviceRow({
  device,
  userId,
  ownerName,
}: {
  device: Device;
  userId?: string;
  ownerName?: string;
}) {
  const queryClient = useQueryClient();
  const listKey = userId ? userDevicesQuery(userId).queryKey : devicesQuery.queryKey;

  async function revoke() {
    const { error, response } = userId
      ? await api.DELETE("/api/v1/users/{user_id}/devices/{device_id}", {
          params: { path: { user_id: userId, device_id: device.id } },
        })
      : await api.DELETE("/api/v1/devices/{device_id}", {
          params: { path: { device_id: device.id } },
        });
    // Also after a failure (e.g. already revoked elsewhere): the list shows the truth.
    await queryClient.invalidateQueries({ queryKey: listKey });
    if (!response.ok) throw new ApiFailure(error);
  }

  return (
    <tr>
      <th scope="row" className="cell-strong">
        {device.name}
      </th>
      <td>{device.app_version || <span className="muted">{t`Onbekend`}</span>}</td>
      <td className="num">
        <time dateTime={device.created_at}>{dateTime.format(new Date(device.created_at))}</time>
      </td>
      <td>
        {/* The list only holds devices that have not been revoked. */}
        <Badge intent="success">{t`Actief`}</Badge>
      </td>
      <td className="cell-action">
        <ConfirmAction
          label={t`Intrekken`}
          ariaLabel={t`Intrekken: ${device.name}`}
          icon="dismiss"
          title={t`Apparaat intrekken?`}
          confirmLabel={t`Intrekken`}
          onConfirm={revoke}
        >
          <p>
            {ownerName
              ? t`${device.name} kan daarna niet meer bij Brievie. ${ownerName} kan het apparaat opnieuw koppelen.`
              : t`${device.name} kan daarna niet meer bij Brievie. Je kunt het apparaat opnieuw koppelen.`}
          </p>
        </ConfirmAction>
      </td>
    </tr>
  );
}

/**
 * The devices of the logged-in user, each with a confirmed "Intrekken". With `userId` it
 * is the list of another account, for an admin who may manage it (`ownerName` is whose).
 */
export function DeviceTable({ userId, ownerName }: { userId?: string; ownerName?: string }) {
  const headingId = useId();
  const { data, error, isPending } = useQuery(userId ? userDevicesQuery(userId) : devicesQuery);

  return (
    <section className="panel" aria-labelledby={headingId}>
      <div className="panel-head">
        <h2 id={headingId}>{t`Gekoppelde apparaten`}</h2>
        {data ? (
          <span className="panel-meta">
            {plural(data.length, { one: "# apparaat", other: "# apparaten" })}
          </span>
        ) : null}
      </div>
      {isPending ? (
        <div className="panel-note">
          <Loading />
        </div>
      ) : null}
      {error ? (
        <div className="panel-note">
          <ErrorAlert error={error} />
        </div>
      ) : null}
      {data && data.length === 0 ? (
        <EmptyState icon="phone">{t`Er zijn nog geen apparaten gekoppeld.`}</EmptyState>
      ) : null}
      {data && data.length > 0 ? (
        // biome-ignore lint/a11y/noNoninteractiveTabindex: a scrolling box must be reachable by keyboard (WCAG 2.1.1).
        <section className="table-scroll" aria-labelledby={headingId} tabIndex={0}>
          <table className="table table-wide">
            <thead>
              <tr>
                <th scope="col">{t`Apparaat`}</th>
                <th scope="col">{t`App-versie`}</th>
                <th scope="col">{t`Gekoppeld op`}</th>
                <th scope="col">{t`Status`}</th>
                <th scope="col" className="cell-action">
                  {t`Actie`}
                </th>
              </tr>
            </thead>
            <tbody>
              {data.map((device) => (
                <DeviceRow key={device.id} device={device} userId={userId} ownerName={ownerName} />
              ))}
            </tbody>
          </table>
        </section>
      ) : null}
    </section>
  );
}
