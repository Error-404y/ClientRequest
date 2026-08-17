import discord 
from discord .ext import commands 
import os 
import html 
import pytz 
from datetime import datetime 
import config 
import aiohttp 
import zipfile 
import shutil 
import re 
from utils .logger import log_exception, log_transcript 

timezone =pytz .timezone (config.TIMEZONE)

async def download_file (url ,destination ):
    try :
        async with aiohttp .ClientSession ()as session :
            async with session .get (url )as response :
                if response .status ==200 :
                    with open (destination ,'wb')as f :
                        f .write (await response .read ())
                    return True 
    except Exception as error:
        log_exception(
            "TRANSCRIPT",
            error,
            context=f"Failed to download transcript asset to {destination}",
        )
    return False 

def parse_markdown (text ):
    if not text :
        return ""
        
    text =html .escape (text )

    
    text =re .sub (r'```(?:[a-zA-Z0-9]+)?\n?(.*?)\n?```',r'<pre class="code-block"><code>\1</code></pre>',text ,flags =re .DOTALL )

    
    text =re .sub (r'`([^`\n]+)`',r'<code class="inline-code">\1</code>',text )

    
    text =re .sub (r'\*\*([^*]+)\*\*',r'<strong>\1</strong>',text )

    
    text =re .sub (r'\*([^*]+)\*',r'<em>\1</em>',text )
    text =re .sub (r'\b_([^_]+)_\b',r'<em>\1</em>',text )

    
    text =re .sub (r'__([^_]+)__',r'<u>\1</u>',text )

    
    text =re .sub (r'~~([^~]+)~~',r'<del>\1</del>',text )

    
    text =re .sub (r'&lt;@!?(\d+)&gt;',r'<span class="mention">@User(\1)</span>',text )
    text =re .sub (r'&lt;@&amp;(\d+)&gt;',r'<span class="mention">@Role(\1)</span>',text )
    text =re .sub (r'&lt;#(\d+)&gt;',r'<span class="mention">#Channel(\1)</span>',text )

    
    text =re .sub (r'\[([^\]]+)\]\((https?://[^\s]+)\)',r'<a href="\2" target="_blank" class="content-link">\1</a>',text )

    
    url_pattern =re .compile (r'(?<!href=")(?<!src=")(https?://[^\s<]+)')
    text =url_pattern .sub (r'<a href="\1" target="_blank" class="content-link">\1</a>',text )

    
    text =text .replace ('\n','<br>')
    return text 

async def create_transcript (channel ,lightweight =False ):
    log_transcript ("Initiated creation",channel ,details =f"Lightweight: {lightweight }")
    
    transcript_dir =f"{config .TRANSCRIPT_FOLDER }/transcript-{channel .name }-{channel .id }"
    avatars_dir =f"{transcript_dir }/avatars"
    attachments_dir =f"{transcript_dir }/attachments"

    if not lightweight :
        os .makedirs (avatars_dir ,exist_ok =True )
        os .makedirs (attachments_dir ,exist_ok =True )
    else :
        os .makedirs (transcript_dir ,exist_ok =True )

        
    user_id =None 
    if channel .topic and "ticket_owner:"in channel .topic :
        try :
            topic_part =channel .topic .split ("|")[0 ].strip ()
            user_id =int (topic_part .replace ("ticket_owner:","").strip ())
        except ValueError :
            user_id =None
    if user_id is None :
        try :
            from utils .database import get_ticket_owner 
            user_id =await get_ticket_owner (channel .id )
        except Exception as error:
            log_exception(
                "DATABASE",
                error,
                guild=channel.guild,
                channel=channel,
                context="Failed to resolve transcript ticket owner",
            )

    messages =[]
    guild =channel .guild 
    downloaded_avatars ={}

    async for message in channel .history (limit =None ,oldest_first =True ):
        content =message .clean_content 
        content_html =parse_markdown (content )if content else ""

        
        member =guild .get_member (message .author .id )

        
        msg_class ="msg-user"
        if message .author .bot :
            msg_class ="msg-bot"
        elif message .author .id ==user_id :
            msg_class ="msg-applicant"

            
        badge_html =""
        from utils.permissions import is_owner
        if (member and is_owner(member)) or message.author.id == config.SETUP_USER_ID:
            badge_html ='<span class="user-badge badge-owner">Owner</span>'
            msg_class ="msg-owner"
        elif member :
            from utils .permissions import is_moderator ,is_trial_moderator 
            if is_moderator (member ):
                badge_html ='<span class="user-badge badge-staff">Staff</span>'
                msg_class ="msg-staff"
            elif is_trial_moderator (member ):
                badge_html ='<span class="user-badge badge-staff" style="background-color: #9b59b6;">Trial Mod</span>'
                msg_class ="msg-staff"

                
        if not lightweight :
            author_id =message .author .id 
            if author_id not in downloaded_avatars :
                avatar_url =message .author .display_avatar .url 
                avatar_filename =f"avatars/{author_id }.png"
                local_avatar_path =f"{transcript_dir }/{avatar_filename }"

                success =await download_file (avatar_url ,local_avatar_path )
                if success :
                    downloaded_avatars [author_id ]=avatar_filename 
                else :
                    downloaded_avatars [author_id ]=avatar_url 

            avatar_src =downloaded_avatars [author_id ]
        else :
            avatar_src =message .author .display_avatar .url 

        timestamp =message .created_at .astimezone (timezone ).strftime ("%d.%m.%Y %H:%M:%S")

        
        attachments_html =""
        for attachment in message .attachments :
            if not lightweight :
            
                safe_filename ="".join (c for c in attachment .filename if c .isalnum ()or c in "._-").strip ()
                if not safe_filename :
                    safe_filename =str (attachment .id )

                local_filename =f"{attachment .id }_{safe_filename }"
                attachment_path =f"attachments/{local_filename }"
                local_attachment_path =f"{transcript_dir }/{attachment_path }"

                
                success =await download_file (attachment .url ,local_attachment_path )
                link_target =attachment_path if success else attachment .url 
            else :
                link_target =attachment .url 

                
            is_image =any (attachment .filename .lower ().endswith (ext )for ext in [".png",".jpg",".jpeg",".gif",".webp"])

            if is_image :
                attachments_html +=f"""
                <div class="attachment attachment-image" style="margin-top: 6px;">
                    <span class="attachment-label" style="font-size: 13px; font-weight: 600; color: #b5bac1; display: block; margin-bottom: 4px;">Image Preview ({html .escape (attachment .filename )}):</span>
                    <a href="{link_target }" target="_blank">
                        <img src="{link_target }" class="image-preview" style="max-width: 100%; max-height: 350px; border-radius: 8px; border: 1px solid #232428;">
                    </a>
                </div>
                """
            else :
                attachments_html +=f"""
                <div class="attachment">
                    <span class="attachment-label">File attachment:</span>
                    <a href="{link_target }" target="_blank" class="attachment-link">
                        {html .escape (attachment .filename )}
                    </a>
                </div>
                """

                
        embeds_html =""
        for embed in message .embeds :
            embed_title =html .escape (embed .title or "")
            embed_desc =html .escape (embed .description or "")

            
            embed_desc_html =parse_markdown (embed .description )if embed .description else ""

            embed_fields_html =""
            for field in embed .fields :
                field_val_html =parse_markdown (field .value )if field .value else ""
                embed_fields_html +=f"""
                <div class="embed-field {"embed-field-inline"if field .inline else ""}">
                    <div class="embed-field-name">{html .escape (field .name or "")}</div>
                    <div class="embed-field-value">{field_val_html }</div>
                </div>
                """

            color_hex ="#5865f2"
            if embed .color :
                color_hex =f"#{embed .color .value :06x}"

            embeds_html +=f"""
            <div class="embed-card" style="border-left: 4px solid {color_hex };">
                {f'<div class="embed-title">{embed_title }</div>'if embed_title else ''}
                {f'<div class="embed-description">{embed_desc_html }</div>'if embed_desc_html else ''}
                {f'<div class="embed-fields">{embed_fields_html }</div>'if embed_fields_html else ''}
            </div>
            """

            
        if not content_html and not attachments_html and not embeds_html :
            content_html ="[No message content]"

        messages .append (f"""
        <div class="message {msg_class }">
            <img src="{avatar_src }" class="avatar">
            <div class="message-body">
                <div class="message-header">
                    <span class="username">{html .escape (message .author .display_name )}</span>
                    {badge_html }
                    <span class="time">{timestamp }</span>
                </div>
                <div class="message-content">
                    {f'<div class="content">{content_html }</div>'if content_html else ''}
                    {attachments_html }
                    {embeds_html }
                </div>
            </div>
        </div>
        """)

        
    page =f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>! maja ! Ticket Transcript</title>
    <style>
        body {{
            background-color: #1e1f22;
            color: #dbdee1;
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            margin: 0;
            padding: 0;
        }}
        .container {{
            max-width: 900px;
            margin: 40px auto;
            background-color: #313338;
            border-radius: 12px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
            overflow: hidden;
            border: 1px solid #3f4147;
        }}
        .header {{
            background: linear-gradient(135deg, #2b2d31 0%, #1e1f22 100%);
            padding: 30px;
            border-bottom: 1px solid #3f4147;
        }}
        .header h1 {{
            margin: 0;
            color: #f2f3f5;
            font-size: 24px;
            font-weight: 700;
        }}
        .header-meta {{
            margin-top: 12px;
            display: flex;
            flex-wrap: wrap;
            gap: 20px;
            font-size: 14px;
            color: #949ba4;
        }}
        .meta-item {{
            display: flex;
        }}
        .meta-label {{
            font-weight: 600;
            color: #b5bac1;
            margin-right: 6px;
        }}
        .controls-panel {{
            padding: 16px 24px;
            background-color: #2b2d31;
            border-bottom: 1px solid #3f4147;
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 16px;
            flex-wrap: wrap;
        }}
        .search-box {{
            flex-grow: 1;
            min-width: 200px;
        }}
        .search-box input {{
            width: 100%;
            background-color: #1e1f22;
            border: 1px solid #1e1f22;
            border-radius: 4px;
            padding: 8px 12px;
            color: #dbdee1;
            font-size: 14px;
            box-sizing: border-box;
            outline: none;
            transition: border-color 0.2s;
        }}
        .search-box input:focus {{
            border-color: #5865f2;
        }}
        .filter-buttons {{
            display: flex;
            gap: 8px;
        }}
        .filter-btn {{
            background-color: #35363c;
            border: none;
            color: #dbdee1;
            padding: 6px 12px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 13px;
            font-weight: 600;
            transition: background-color 0.2s, color 0.2s;
        }}
        .filter-btn:hover {{
            background-color: #4e5058;
            color: #fff;
        }}
        .filter-btn.active {{
            background-color: #5865f2;
            color: #fff;
        }}
        .chat-area {{
            padding: 24px;
            display: flex;
            flex-direction: column;
        }}
        .message {{
            display: flex;
            margin-bottom: 20px;
            gap: 16px;
            padding: 4px 8px;
            border-radius: 4px;
            transition: background-color 0.1s ease;
        }}
        .message:hover {{
            background-color: #2e3035;
        }}
        .avatar {{
            width: 40px;
            height: 40px;
            border-radius: 50%;
            object-fit: cover;
            background-color: #2b2d31;
            flex-shrink: 0;
        }}
        .message-body {{
            flex-grow: 1;
            min-width: 0;
        }}
        .message-header {{
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 4px;
        }}
        .username {{
            font-weight: 600;
            color: #f2f3f5;
            font-size: 15px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .user-badge {{
            font-size: 10px;
            font-weight: 700;
            padding: 1px 5px;
            border-radius: 3px;
            text-transform: uppercase;
            line-height: 1.2;
        }}
        .badge-owner {{
            background-color: #f5b041;
            color: #1e1f22;
        }}
        .badge-staff {{
            background-color: #5865f2;
            color: #ffffff;
        }}
        .time {{
            color: #949ba4;
            font-size: 12px;
        }}
        .message-content {{
            display: flex;
            flex-direction: column;
            gap: 4px;
        }}
        .content {{
            font-size: 15px;
            line-height: 1.5;
            color: #dbdee1;
            white-space: pre-wrap;
            word-break: break-word;
        }}
        .inline-code {{
            background-color: #2b2d31;
            padding: .2em .4em;
            margin: 0;
            font-size: 85%;
            white-space: break-spaces;
            border-radius: 3px;
            font-family: Consolas, "Andale Mono WT", monospace;
        }}
        .code-block {{
            background-color: #2b2d31;
            border: 1px solid #1e1f22;
            border-radius: 4px;
            padding: 8px 12px;
            font-size: 14px;
            overflow-x: auto;
            margin: 6px 0;
        }}
        .code-block code {{
            font-family: Consolas, "Andale Mono WT", monospace;
            color: #dbdee1;
            white-space: pre-wrap;
        }}
        .content-link {{
            color: #00a8fc;
            text-decoration: none;
        }}
        .content-link:hover {{
            text-decoration: underline;
        }}
        .mention {{
            background-color: rgba(88, 101, 242, 0.3);
            color: #c9cdfb;
            font-weight: 500;
            padding: 0 2px;
            border-radius: 3px;
        }}
        .attachment {{
            margin-top: 6px;
            padding: 8px 12px;
            background-color: #2b2d31;
            border: 1px solid #3f4147;
            border-radius: 6px;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            align-self: flex-start;
        }}
        .attachment-label {{
            font-size: 13px;
            font-weight: 600;
            color: #b5bac1;
        }}
        .attachment-link {{
            color: #00a8fc;
            text-decoration: none;
            font-size: 13px;
        }}
        .attachment-link:hover {{
            text-decoration: underline;
        }}
        .embed-card {{
            background-color: #2b2d31;
            border-radius: 4px;
            padding: 12px 16px;
            margin-top: 6px;
            max-width: 520px;
            border: 1px solid #232428;
        }}
        .embed-title {{
            font-weight: 600;
            font-size: 15px;
            color: #f2f3f5;
            margin-bottom: 6px;
        }}
        .embed-description {{
            font-size: 14px;
            color: #dbdee1;
            line-height: 1.4;
        }}
        .embed-fields {{
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            margin-top: 10px;
        }}
        .embed-field {{
            flex: 1 1 100%;
        }}
        .embed-field-inline {{
            flex: 1 1 30%;
            min-width: 120px;
        }}
        .embed-field-name {{
            font-size: 12px;
            font-weight: 700;
            color: #b5bac1;
            margin-bottom: 2px;
            text-transform: uppercase;
        }}
        .embed-field-value {{
            font-size: 14px;
            color: #dbdee1;
        }}
        .footer {{
            text-align: center;
            padding: 20px;
            background-color: #2b2d31;
            color: #949ba4;
            font-size: 12px;
            border-top: 1px solid #3f4147;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>! maja ! Ticket Transcript</h1>
            <div class="header-meta">
                <div class="meta-item">
                    <span class="meta-label">Server:</span>
                    <span>{html .escape (guild .name )}</span>
                </div>
                <div class="meta-item">
                    <span class="meta-label">Channel:</span>
                    <span>{html .escape (channel .name )}</span>
                </div>
                <div class="meta-item">
                    <span class="meta-label">Generated At:</span>
                    <span>{datetime .now (timezone ).strftime ("%d.%m.%Y %H:%M:%S")}</span>
                </div>
            </div>
        </div>
        <div class="controls-panel">
            <div class="search-box">
                <input type="text" id="search-input" placeholder="Search messages or users...">
            </div>
            <div class="filter-buttons">
                <button onclick="filterMessages('all')" id="filter-all" class="filter-btn active">All</button>
                <button onclick="filterMessages('staff')" id="filter-staff" class="filter-btn">Staff</button>
                <button onclick="filterMessages('applicant')" id="filter-applicant" class="filter-btn">Applicant</button>
                <button onclick="filterMessages('bot')" id="filter-bot" class="filter-btn">Bots/System</button>
            </div>
        </div>
        <div class="chat-area">
            {"".join (messages )}
        </div>
        <div class="footer">
            Generated by ! maja !
        </div>
    </div>

    <script>
        function filterMessages(filter) {{
            const messages = document.querySelectorAll('.message');
            const searchVal = document.getElementById('search-input').value.toLowerCase();
            
            messages.forEach(msg => {{
                let isVisible = true;
                
                if (filter === 'staff') {{
                    isVisible = msg.classList.contains('msg-staff') || msg.classList.contains('msg-owner');
                }} else if (filter === 'applicant') {{
                    isVisible = msg.classList.contains('msg-applicant');
                }} else if (filter === 'bot') {{
                    isVisible = msg.classList.contains('msg-bot');
                }}
                
                if (isVisible && searchVal) {{
                    const contentElement = msg.querySelector('.content');
                    const text = contentElement ? contentElement.textContent.toLowerCase() : '';
                    const username = msg.querySelector('.username').textContent.toLowerCase();
                    isVisible = text.includes(searchVal) || username.includes(searchVal);
                }}
                
                msg.style.display = isVisible ? 'flex' : 'none';
            }});
            
            document.querySelectorAll('.filter-btn').forEach(btn => {{
                btn.classList.remove('active');
            }});
            document.getElementById('filter-' + filter).classList.add('active');
        }}

        document.getElementById('search-input').addEventListener('input', () => {{
            const activeBtn = document.querySelector('.filter-btn.active');
            const activeFilter = activeBtn ? activeBtn.id.replace('filter-', '') : 'all';
            filterMessages(activeFilter);
        }});
    </script>
</body>
</html>
"""

    html_file =f"{transcript_dir }/index.html"
    with open (html_file ,"w",encoding ="utf-8")as file :
        file .write (page )

        
    zip_filename =f"{config .TRANSCRIPT_FOLDER }/{channel .name }.zip"

    with zipfile .ZipFile (zip_filename ,'w',zipfile .ZIP_DEFLATED )as zip_file :
        for root ,dirs ,files in os .walk (transcript_dir ):
            for file in files :
                file_path =os .path .join (root ,file )
                arcname =os .path .relpath (file_path ,start =transcript_dir )
                zip_file .write (file_path ,arcname )

                
    try :
        shutil .rmtree (transcript_dir )
    except OSError as error:
        log_exception(
            "TRANSCRIPT",
            error,
            guild=channel.guild,
            channel=channel,
            context="Failed to remove temporary transcript directory",
        )

    size_bytes =os .path .getsize (zip_filename )if os .path .exists (zip_filename )else 0 
    log_transcript ("Zip Archive Created",channel ,details =f"Path: {zip_filename }, Size: {size_bytes } bytes, Messages: {len (messages )}")

    return zip_filename 

class Transcript (commands .Cog ):
    def __init__ (self ,bot ):
        self .bot =bot 

async def setup (bot ):
    await bot .add_cog (Transcript (bot ))
