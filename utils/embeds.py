import discord

import config

PRIMARY = discord.Color.from_rgb(88, 101, 242)
SUCCESS = discord.Color.green()
ERROR = discord.Color.red()
WARNING = discord.Color.orange()


def estimate_response_time(available_staff):
    if available_staff <= 0:
        return "Currently unavailable"
    if available_staff == 1:
        return "20–30 minutes"
    if available_staff == 2:
        return "15–25 minutes"
    if available_staff == 3:
        return "10–20 minutes"
    return "5–15 minutes"


def ticket_panel(
    bot=None, guild=None, available_staff=0, response_time="Currently unavailable"
):
    server_name = guild.name if guild else "Support Portal"
    embed = discord.Embed(
        title=f"{config.BOT_NAME} Support Portal",
        description=(
            f"Welcome to the **{server_name}** support portal. "
            "Please choose the appropriate option from the dropdown menu below to open a private ticket. "
            "Our staff team is available for inquiries, partnerships, player reports, and support requests."
        ),
        color=PRIMARY,
    )
    embed.add_field(
        name="Process",
        value=(
            "1. Select the desired ticket type from the dropdown menu below.\n"
            "2. A private channel will be created for you automatically.\n"
            "3. Describe your inquiry directly in the channel."
        ),
        inline=False,
    )
    embed.add_field(
        name="Guidelines",
        value=(
            "• Please be honest and provide as much detail as possible.\n"
            "• For Player Reports, ensure you attach clear evidence (screenshots, videos, or logs).\n"
            "• Opening multiple redundant tickets is prohibited."
        ),
        inline=False,
    )
    embed.add_field(name="Available Staff", value=f"**{available_staff}**", inline=True)
    embed.add_field(
        name="Estimated Response Time", value=f"**{response_time}**", inline=True
    )
    if bot and bot.user and bot.user.avatar:
        embed.set_thumbnail(url=bot.user.avatar.url)
    embed.set_footer(text=f"{server_name} Operations | {config.BOT_CREDITS}")
    return embed


def ticket_created(user, application, form, ticket_uuid=None, custom_answers=None):
    title = "Support Ticket Created" if not form else "Application Ticket Initiated"
    desc = "Hello! Your private ticket has been successfully created. Please describe your request below."

    if application == "Partnership":
        desc = "Hello! Your partnership ticket has been created. Please describe your partnership proposal, including your community link, member count, and what you expect from this partnership."
    elif application == "Player Reports":
        desc = "Hello! Your player report ticket has been created. Please provide the offender's username/ID, a detailed description of the incident, and any evidence (screenshots, videos, or clips)."
    elif application == "Billing/Issues":
        desc = "Hello! Your support ticket has been created. Please describe the technical issue or billing query you are experiencing in detail."
    elif form:
        desc = "Hello! Your private application ticket has been successfully created. Please follow the instructions below to complete your request."

    embed = discord.Embed(title=title, description=desc, color=PRIMARY)
    embed.add_field(name="User", value=user.mention, inline=True)
    embed.add_field(name="Ticket Category", value=application, inline=True)
    embed.add_field(name="Ticket Label", value="Not assigned", inline=True)

    if ticket_uuid:
        embed.add_field(name="Ticket UUID", value=f"`{ticket_uuid}`", inline=False)

    if custom_answers:
        for response in custom_answers[:5]:
            embed.add_field(
                name=str(response.get("question") or "Question")[:256],
                value=str(response.get("answer") or "Not provided")[:1024],
                inline=False,
            )

    if form:
        embed.add_field(
            name="Form Link", value=f"[Click Here to Open Form]({form})", inline=False
        )
        embed.add_field(
            name="Instructions",
            value="Please click the link above to fill out your form. Once submitted, post a confirmation message in this channel so our staff team can proceed with your evaluation.",
            inline=False,
        )
    elif custom_answers:
        embed.add_field(
            name="Instructions",
            value="Your form responses are recorded above. Add any supporting evidence or additional context in this channel.",
            inline=False,
        )
    else:
        embed.add_field(
            name="Instructions",
            value="Please write details of your request directly in this channel. Our support team has been notified and will assist you shortly.",
            inline=False,
        )

    embed.set_thumbnail(url=user.display_avatar.url)
    embed.set_footer(text=f"{config.BOT_NAME} | Private Channel")
    return embed


def apply_ticket_label(embed, label):
    value = label or "Not assigned"
    for index, field in enumerate(embed.fields):
        if field.name.casefold() == "ticket label":
            embed.set_field_at(index, name="Ticket Label", value=value, inline=True)
            return embed
    embed.add_field(name="Ticket Label", value=value, inline=True)
    return embed


def ticket_claimed_dm(guild, channel, staff, bot_user=None):
    embed = discord.Embed(
        title="Your Ticket Is Now Under Review",
        description=(
            "A member of the support team has accepted responsibility for your request. "
            "Your ticket is now being reviewed and any further communication will continue in the private ticket channel."
        ),
        color=PRIMARY,
        timestamp=discord.utils.utcnow(),
    )
    guild_icon = getattr(getattr(guild, "icon", None), "url", None)
    bot_icon = getattr(getattr(bot_user, "display_avatar", None), "url", None)
    branding_icon = guild_icon or bot_icon
    if branding_icon:
        embed.set_author(
            name=f"{guild.name} | Support Operations", icon_url=branding_icon
        )
    else:
        embed.set_author(name=f"{guild.name} | Support Operations")
    embed.add_field(name="Current Status", value="Under Review", inline=True)
    embed.add_field(name="Ticket Reference", value=f"#{channel.name}", inline=True)
    embed.add_field(name="Assigned Staff", value=staff.display_name, inline=True)
    embed.add_field(
        name="What Happens Next",
        value=(
            "The assigned staff member will review the information already provided. "
            "Please keep notifications enabled and respond in the ticket channel if additional details are requested."
        ),
        inline=False,
    )
    embed.add_field(
        name="Important Information",
        value="This is an automated status notification. Reply inside your ticket channel instead of responding to this direct message.",
        inline=False,
    )
    if bot_icon:
        embed.set_thumbnail(url=bot_icon)
    embed.set_footer(text=f"{config.BOT_NAME} | Ticket Lifecycle Notification")
    return embed


def ticket_closed_dm(
    guild, channel, moderator, reason, transcript_attached, bot_user=None
):
    transcript_status = (
        "Attached to this message as a downloadable archive."
        if transcript_attached
        else "A transcript could not be attached to this notification. Contact the support team if you require a copy."
    )
    embed = discord.Embed(
        title="Your Ticket Has Been Closed",
        description=(
            "The support team has completed the active handling of your request and the private ticket channel has been archived. "
            "The closure details are provided below for your records."
        ),
        color=SUCCESS,
        timestamp=discord.utils.utcnow(),
    )
    guild_icon = getattr(getattr(guild, "icon", None), "url", None)
    bot_icon = getattr(getattr(bot_user, "display_avatar", None), "url", None)
    branding_icon = guild_icon or bot_icon
    if branding_icon:
        embed.set_author(
            name=f"{guild.name} | Support Operations", icon_url=branding_icon
        )
    else:
        embed.set_author(name=f"{guild.name} | Support Operations")
    embed.add_field(name="Final Status", value="Closed and Archived", inline=True)
    embed.add_field(name="Ticket Reference", value=f"#{channel.name}", inline=True)
    embed.add_field(name="Closed By", value=moderator.display_name, inline=True)
    embed.add_field(
        name="Closure Reason", value=reason or "No reason was provided.", inline=False
    )
    embed.add_field(name="Transcript", value=transcript_status, inline=False)
    embed.add_field(
        name="Need Further Assistance",
        value=(
            "If the matter is unresolved or you need additional support, open a new ticket through the official ticket panel "
            "and reference the ticket name shown above."
        ),
        inline=False,
    )
    if bot_icon:
        embed.set_thumbnail(url=bot_icon)
    embed.set_footer(text=f"{config.BOT_NAME} | Ticket Closure Record")
    return embed


def ticket_closed(moderator, reason="No reason provided", applicant=None):
    embed = discord.Embed(
        title="Ticket Closed & Archived",
        description="This application ticket has been closed by staff. The applicant has been removed from the channel.",
        color=WARNING,
    )
    embed.add_field(name="Closed By", value=moderator.mention, inline=True)
    embed.add_field(name="Status", value="Archived", inline=True)
    embed.add_field(name="Close Reason", value=reason, inline=False)
    if applicant:
        embed.add_field(
            name="Applicant",
            value=f"{applicant.mention} (`{applicant.id}`)",
            inline=False,
        )
        embed.set_thumbnail(url=applicant.display_avatar.url)
    else:
        embed.set_thumbnail(url=moderator.display_avatar.url)
    embed.add_field(
        name="Actions",
        value="Authorized administrators can view/save the transcript or delete the channel using the controls below.",
        inline=False,
    )
    embed.set_footer(text=f"{config.BOT_NAME} | Audit Log")
    return embed


def ticket_reopened(applicant=None):
    embed = discord.Embed(
        title="Ticket Reopened",
        description="The ticket channel has been reopened. The applicant has been added back to the channel permissions.",
        color=SUCCESS,
    )
    embed.add_field(name="Status", value="Active / Reopened", inline=True)
    embed.add_field(
        name="Channel permissions", value="Restored to original state", inline=True
    )
    if applicant:
        embed.add_field(
            name="Applicant",
            value=f"{applicant.mention} (`{applicant.id}`)",
            inline=False,
        )
        embed.set_thumbnail(url=applicant.display_avatar.url)
    embed.set_footer(text=config.BOT_NAME)
    return embed


def error(message):
    embed = discord.Embed(title="Request Denied", description=message, color=ERROR)
    embed.set_footer(text=config.BOT_NAME)
    return embed


def success(message):
    embed = discord.Embed(title="Request Processed", description=message, color=SUCCESS)
    embed.set_footer(text=config.BOT_NAME)
    return embed
