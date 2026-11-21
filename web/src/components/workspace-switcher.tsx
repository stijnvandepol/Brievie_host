import { t } from "@lingui/core/macro";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import {
  Button,
  Menu,
  MenuItem,
  MenuSection,
  MenuTrigger,
  Popover,
  Separator,
} from "react-aria-components";
import { workspacesQuery } from "../api/queries";
import { Icon } from "./icons";
import { initials } from "./ui";
import { kindLabel, roleLabel } from "./workspace-labels";

export type SwitcherWorkspace = { id: string; name: string; kind?: string; role?: string };

const ALL = "__all__";

/** The workspace's initial in a coloured square, its name and its kind. */
function Identity({ workspace }: { workspace: SwitcherWorkspace }) {
  return (
    <>
      <span className="ws-initial" aria-hidden="true">
        {initials(workspace.name).slice(0, 1)}
      </span>
      <span className="ws-text">
        <span className="ws-name">{workspace.name}</span>
        {workspace.kind ? <span className="ws-kind">{kindLabel(workspace.kind)}</span> : null}
      </span>
    </>
  );
}

/**
 * The top of the menu (spec S 4.3): which workspace this is. With one workspace a plain
 * block, never a button that does nothing (the old dead "Werkruimtes", S 2.1). With several
 * a menu of them, the current one checked, no counts of the others (each is its own space);
 * choosing another opens its Mijn post, choosing the current one only closes the menu.
 */
export function WorkspaceSwitcher({ current }: { current: SwitcherWorkspace }) {
  const navigate = useNavigate();
  const { data: list } = useQuery(workspacesQuery);
  if (!list || list.length <= 1) {
    return (
      <div className="ws-switcher ws-switcher-single">
        <Identity workspace={current} />
      </div>
    );
  }
  function open(id: string) {
    if (id === current.id) return;
    void navigate({ to: "/w/$workspaceId/mijn-post", params: { workspaceId: id } });
  }
  return (
    <MenuTrigger>
      <Button
        className="ws-switcher"
        aria-label={t`Werkruimte: ${current.name}. Kies een andere werkruimte`}
      >
        <Identity workspace={current} />
        <Icon name="chevronDown" className="ws-chevron" />
      </Button>
      <Popover className="popover ws-popover" placement="bottom start" offset={4}>
        <Menu
          className="ws-menu"
          aria-label={t`Werkruimte kiezen`}
          onAction={(key) => {
            if (key === ALL) void navigate({ to: "/werkruimtes" });
          }}
        >
          <MenuSection
            selectionMode="single"
            disallowEmptySelection
            selectedKeys={[current.id]}
            onSelectionChange={(keys) => {
              const [picked] = [...(keys === "all" ? [] : keys)];
              if (typeof picked === "string") open(picked);
            }}
          >
            {list.map((workspace) => (
              <MenuItem
                key={workspace.id}
                id={workspace.id}
                textValue={workspace.name}
                className="menu-item ws-item"
              >
                {({ isSelected }) => (
                  <>
                    <Identity workspace={workspace} />
                    <span className="ws-role">{roleLabel(workspace.role)}</span>
                    {isSelected ? <Icon name="checkmark" className="ws-check" /> : null}
                  </>
                )}
              </MenuItem>
            ))}
          </MenuSection>
          <Separator />
          <MenuSection>
            <MenuItem id={ALL} textValue={t`Alle werkruimtes`} className="menu-item">
              <Icon name="grid" />
              {t`Alle werkruimtes`}
            </MenuItem>
          </MenuSection>
        </Menu>
      </Popover>
    </MenuTrigger>
  );
}
