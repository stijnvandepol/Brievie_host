import { plural, t } from "@lingui/core/macro";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId } from "react";
import { Button, Dialog, DialogTrigger, Heading, Modal, ModalOverlay } from "react-aria-components";
import { ApiFailure, api } from "../api/client";
import { devicesQuery } from "../api/queries";
import type { components } from "../api/schema";
import { Icon } from "./icons";
import { ErrorAlert } from "./page";
import { Badge, EmptyState, Loading } from "./ui";

type Device = components["schemas"]["DeviceOut"];

const dateTime = new Intl.DateTimeFormat("nl-NL", { dateStyle: "medium", timeStyle: "short" });

function DeviceRow({ device }: { device: Device }) {
  const queryClient = useQueryClient();
  const revoke = useMutation({
    mutationFn: async () => {
      const { error, response } = await api.DELETE("/api/v1/devices/{device_id}", {
        params: { path: { device_id: device.id } },
      });
      if (!response.ok) throw new ApiFailure(error);
    },
    // Also after a failure (e.g. already revoked elsewhere): the list shows the truth.
    onSettled: () => queryClient.invalidateQueries({ queryKey: devicesQuery.queryKey }),
  });

  return (
    <tr>
      <th scope="row" className="cell-strong">
        {device.name}
      </th>
      <td className="num">
        <time dateTime={device.created_at}>{dateTime.format(new Date(device.created_at))}</time>
      </td>
      <td>
        {/* The list only holds devices that have not been revoked. */}
        <Badge intent="success">{t`Actief`}</Badge>
      </td>
      <td className="cell-action">
        <DialogTrigger>
          <Button className="button-text danger" aria-label={t`Intrekken: ${device.name}`}>
            <Icon name="dismiss" />
            {t`Intrekken`}
          </Button>
          <ModalOverlay isDismissable className="overlay">
            <Modal className="modal">
              <Dialog role="alertdialog" className="dialog">
                {({ close }) => (
                  <>
                    <Heading slot="title" className="dialog-title">
                      {t`Apparaat intrekken?`}
                    </Heading>
                    <p>{t`${device.name} kan daarna niet meer bij Brievie. Je kunt het apparaat opnieuw koppelen.`}</p>
                    <div className="dialog-actions">
                      <Button className="button secondary" onPress={close}>
                        {t`Annuleren`}
                      </Button>
                      <Button
                        className="button danger-solid"
                        onPress={() => {
                          revoke.mutate();
                          close();
                        }}
                      >
                        {t`Intrekken`}
                      </Button>
                    </div>
                  </>
                )}
              </Dialog>
            </Modal>
          </ModalOverlay>
        </DialogTrigger>
        {revoke.isError ? (
          <div role="alert" className="field-error">
            {revoke.error.message}
          </div>
        ) : null}
      </td>
    </tr>
  );
}

/** The devices of the logged-in user, each with a confirmed "Intrekken". */
export function DeviceTable() {
  const headingId = useId();
  const { data, error, isPending } = useQuery(devicesQuery);

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
                <th scope="col">{t`Gekoppeld op`}</th>
                <th scope="col">{t`Status`}</th>
                <th scope="col" className="cell-action">
                  {t`Actie`}
                </th>
              </tr>
            </thead>
            <tbody>
              {data.map((device) => (
                <DeviceRow key={device.id} device={device} />
              ))}
            </tbody>
          </table>
        </section>
      ) : null}
    </section>
  );
}
