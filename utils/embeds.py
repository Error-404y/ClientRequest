import discord
import config


PRIMARY = discord.Color.from_rgb(88, 101, 242)  
SUCCESS = discord.Color.green()                 
ERROR = discord.Color.red()                     
WARNING = discord.Color.orange()                 


def ticket_panel(bot=None, guild=None):
    server_name = guild.name if guild else "Zer's Lobby"
    embed = discord.Embed(
        title="ZER's Support Bot",
        description=(
            f"Welcome to the **{server_name}** support portal. "
            "Please select the appropriate option from the dropdown menu below to open a private ticket. "
            "Our staff team is available for inquiries, partnerships, player reports, and support requests."
        ),
        color=PRIMARY
    )
    embed.add_field(
        name="Process",
        value=(
            "1. Select the desired ticket type from the dropdown menu below.\n"
            "2. A private channel will be created for you automatically.\n"
            "3. Describe your inquiry directly in the channel."
        ),
        inline=False
    )
    embed.add_field(
        name="Guidelines",
        value=(
            "• Please be honest and provide as much detail as possible.\n"
            "• For Player Reports, ensure you attach clear evidence (screenshots, videos, or logs).\n"
            "• Opening multiple redundant tickets is prohibited."
        ),
        inline=False
    )
    if bot and bot.user and bot.user.avatar:
        embed.set_thumbnail(url=bot.user.avatar.url)
    embed.set_footer(text=f"{server_name} Operations • Select ticket type below")
    return embed

def ticket_created(user, application, form, ticket_uuid=None):
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

    embed = discord.Embed(
        title=title,
        description=desc,
        color=PRIMARY
    )
    embed.add_field(
        name="User",
        value=user.mention,
        inline=True
    )
    embed.add_field(
        name="Ticket Category",
        value=application,
        inline=True
    )
    
    if ticket_uuid:
        embed.add_field(
            name="Ticket UUID",
            value=f"`{ticket_uuid}`",
            inline=False
        )
    
    if form:
        embed.add_field(
            name="Form Link",
            value=f"[Click Here to Open Form]({form})",
            inline=False
        )
        embed.add_field(
            name="Instructions",
            value="Please click the link above to fill out your form. Once submitted, post a confirmation message in this channel so our staff team can proceed with your evaluation.",
            inline=False
        )
    else:
        embed.add_field(
            name="Instructions",
            value="Please write details of your request directly in this channel. Our support team has been notified and will assist you shortly.",
            inline=False
        )
        
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.set_footer(text="ZER Ticket System • Private Channel")
    return embed


def ticket_closed(moderator, reason="No reason provided", applicant=None):
    embed = discord.Embed(
        title="Ticket Closed & Archived",
        description="This application ticket has been closed by staff. The applicant has been removed from the channel.",
        color=WARNING
    )
    embed.add_field(
        name="Closed By",
        value=moderator.mention,
        inline=True
    )
    embed.add_field(
        name="Status",
        value="Archived",
        inline=True
    )
    embed.add_field(
        name="Close Reason",
        value=reason,
        inline=False
    )
    if applicant:
        embed.add_field(
            name="Applicant",
            value=f"{applicant.mention} (`{applicant.id}`)",
            inline=False
        )
        embed.set_thumbnail(url=applicant.display_avatar.url)
    else:
        embed.set_thumbnail(url=moderator.display_avatar.url)
    embed.add_field(
        name="Actions",
        value="Authorized administrators can view/save the transcript or delete the channel using the controls below.",
        inline=False
    )
    embed.set_footer(text="ZER Ticket System • Audit Log")
    return embed



def ticket_reopened(applicant=None):
    embed = discord.Embed(
        title="Ticket Reopened",
        description="The ticket channel has been reopened. The applicant has been added back to the channel permissions.",
        color=SUCCESS
    )
    embed.add_field(
        name="Status",
        value="Active / Reopened",
        inline=True
    )
    embed.add_field(
        name="Channel permissions",
        value="Restored to original state",
        inline=True
    )
    if applicant:
        embed.add_field(
            name="Applicant",
            value=f"{applicant.mention} (`{applicant.id}`)",
            inline=False
        )
        embed.set_thumbnail(url=applicant.display_avatar.url)
    embed.set_footer(text="ZER Ticket System")
    return embed


def error(message):
    embed = discord.Embed(
        title="Request Denied",
        description=message,
        color=ERROR
    )
    embed.set_footer(text="ZER Ticket System")
    return embed


def success(message):
    embed = discord.Embed(
        title="Request Processed",
        description=message,
        color=SUCCESS
    )
    embed.set_footer(text="ZER Ticket System")
    return embed
