from discord.ext import commands

from cogs.moderation_actions import ModerationActionsCog
from cogs.moderation_history import ModerationHistoryCog
from cogs.moderation_warnings import ModerationWarningsCog


async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationActionsCog(bot))
    await bot.add_cog(ModerationWarningsCog(bot))
    await bot.add_cog(ModerationHistoryCog(bot))
