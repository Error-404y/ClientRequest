# ! maja !

Professional multi-server ticket operations and staff-support system.

## Server Setup

1. Add the bot with the `bot` and `applications.commands` scopes.
2. Grant Manage Channels, View Channels, Send Messages, Embed Links, Attach Files, and Read Message History.
3. Run `/setup start` in the server.
4. Select an existing staff role or allow the bot to create one.
5. Optionally provide comma-separated ticket types.

The setup command creates a public `ticket-system` category containing the `ticket` panel channel, a separate private ticket category, archive category, logging channel, staff role when required, and a working ticket menu.

## Setup Commands

- `/setup start` configures the complete system.
- `/setup status` checks configuration and permissions.
- `/setup admins` lists delegated setup administrators.
- `/add admin` grants a member access to every setup command.
- `/remove admin` revokes delegated setup access.
- `/setup repair` restores missing resources and publishes a fresh panel.
- `/setup reset` clears stored setup information without deleting Discord channels.
- `/help` explains member and staff workflows.
- `/privacy` displays the operational data summary.
- `/invite` creates the official installation link without requesting Administrator.

The server owner, members with Discord Administrator permission, and delegated setup administrators can use setup commands. Only the server owner can add or remove delegated setup administrators.

Use `/setup tickets` and enter comma-separated names such as `Partnership, Issues, Player Reports, Questions` to replace the ticket menu with custom categories.

## Public Installation

Configure Guild Install in the Discord Developer Portal with the Discord-provided installation link. Request only the permissions required by enabled features. Do not grant Administrator.

The bot stores each server's settings and operational records under its Discord server ID. Cross-server update broadcasts and record lookups are prohibited.

## Production Requirements

- Store `DISCORD_TOKEN` only in the deployment environment.
- Use persistent storage with regular backups.
- Run the bot through a process supervisor or container platform.
- Review and publish the included [Privacy Policy](PRIVACY_POLICY.md) and [Terms of Service](TERMS_OF_SERVICE.md) before enabling App Directory discovery.
- Review Message Content, Guild Members, and Guild Presences intent requirements before verification.

Set the published URLs and official support-server invitation in `.env` so they appear in onboarding and `/privacy`:

```text
SUPPORT_SERVER_URL=https://discord.gg/your-invite
PRIVACY_POLICY_URL=https://your-domain.example/privacy
TERMS_OF_SERVICE_URL=https://your-domain.example/terms
```

Created by ! ZER and ! Unbekannt.
