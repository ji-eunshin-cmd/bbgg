import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from dotenv import load_dotenv

from ledger import Ledger, month_bounds, parse_date, week_bounds

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')
ZONE = ZoneInfo(os.getenv('TIMEZONE', 'Asia/Seoul'))
DB_PATH = Path(os.getenv('DATABASE_PATH', 'data/ledger.sqlite3'))
ledger = Ledger(DB_PATH if DB_PATH.is_absolute() else ROOT / DB_PATH)
logging.basicConfig(level=logging.INFO)


async def daily_receipt(guild, user, day_text):
    day = parse_date(day_text)
    week_start, week_end = week_bounds(day)
    month_start, month_end = month_bounds(day)
    daily = await asyncio.to_thread(ledger.summary, guild, user, day_text, day_text)
    weekly = await asyncio.to_thread(ledger.summary, guild, user, week_start, week_end)
    monthly = await asyncio.to_thread(ledger.summary, guild, user, month_start, month_end)
    lines = [
        f'## {day.year}년 {day.month}월 {day.day}일',
        f'**일별 소계: {daily[0]:,}원** · 빵원 성공 {daily[1]}일',
        f'**주간 소계: {weekly[0]:,}원** · 빵원 성공 {weekly[1]}일',
        f'주간 기간: {week_start:%Y-%m-%d} ~ {week_end:%Y-%m-%d}',
        f'**{day.month}월 소계: {monthly[0]:,}원** · 빵원 성공 {monthly[1]}일',
    ]
    if daily[1]:
        lines.append('빵원 성공!')
    return '\n'.join(lines)


async def reply_error(interaction, error):
    message = str(error) if isinstance(error, ValueError) else '처리에 실패했습니다. 잠시 후 다시 시도해 주세요.'
    if not isinstance(error, ValueError):
        logging.error('Interaction failed', exc_info=(type(error), error, error.__traceback__))
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=False)
    else:
        await interaction.response.send_message(message, ephemeral=False)


class PrivateView(discord.ui.View):
    def __init__(self, user):
        super().__init__(timeout=300)
        self.user = user
        self.lock = asyncio.Lock()

    async def interaction_check(self, interaction):
        if interaction.user.id == self.user:
            return True
        await interaction.response.send_message('본인이 실행한 메뉴만 사용할 수 있습니다.', ephemeral=False)
        return False

    async def on_error(self, interaction, error, item):
        await reply_error(interaction, error)


class SaveView(PrivateView):
    def __init__(self, user, guild, day, item, amount, original=None):
        super().__init__(user)
        self.guild, self.day, self.item, self.amount = guild, day, item, amount
        self.original = original
        self.success = bool(original['success']) if original is not None else None
        self.done = False
        if original is not None:
            for option in self.choose.options:
                option.default = (option.value == str(int(self.success)))

    @discord.ui.select(placeholder='빵원 성공 여부를 직접 선택하세요', options=[
        discord.SelectOption(label='빵원 성공', value='1'),
        discord.SelectOption(label='빵원 미성공', value='0')])
    async def choose(self, interaction, select):
        self.success = select.values[0] == '1'
        for option in select.options:
            option.default = option.value == select.values[0]
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label='저장', style=discord.ButtonStyle.green)
    async def save(self, interaction, button):
        async with self.lock:
            if self.done:
                raise ValueError('이미 처리한 입력입니다.')
            if self.success is None:
                raise ValueError('콤보박스에서 빵원 성공 여부를 먼저 선택해 주세요.')
            await interaction.response.defer()
            eid = await asyncio.to_thread(ledger.save, self.guild, self.user, self.day, self.item,
                self.amount, self.success,
                self.original['id'] if self.original is not None else None,
                self.original['version'] if self.original is not None else None)
            self.done = True
            self.stop()
            content = await daily_receipt(self.guild, self.user, self.day)
            content = content.split('\n', 1)[0] + '\n' + discord.utils.escape_markdown(self.item) + f' · {self.amount:,}원\n' + content.split('\n', 1)[1]
            await interaction.edit_original_response(content=content, view=None)
            if self.original is not None and self.original['day'] != self.day:
                previous = await daily_receipt(self.guild, self.user, self.original['day'])
                await interaction.followup.send(previous, ephemeral=False)

    @discord.ui.button(label='취소', style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        async with self.lock:
            self.done = True
            self.stop()
            await interaction.response.edit_message(content='입력을 취소했습니다.', view=None)


class EntryModal(discord.ui.Modal, title='가계부 입력 / 수정'):
    def __init__(self, user, guild, original=None, success=False):
        super().__init__(timeout=300)
        self.user, self.guild, self.original, self.success = user, guild, original, success
        self.day = discord.ui.TextInput(label='날짜 (YYYY-MM-DD)', default=original['day'] if original is not None else datetime.now(ZONE).date().isoformat(), max_length=10)
        self.item = discord.ui.TextInput(label='항목 (빵원 성공이면 생략 가능)' if success else '항목', required=not success, default=original['item'] if original is not None else None, max_length=100)
        self.amount = discord.ui.TextInput(label='금액 (생략하면 0원)' if success else '금액 (원, 0 이상 정수)', required=not success, default=str(original['amount']) if original is not None else None, max_length=13)
        for field in (self.day, self.item, self.amount):
            self.add_item(field)

    async def on_submit(self, interaction):
        from ledger import validate
        raw = str(self.amount).strip()
        if self.success and not raw:
            raw = '0'
        if not raw.isascii() or not raw.isdecimal():
            raise ValueError('금액은 쉼표 없이 0 이상의 정수로 입력해 주세요.')
        item_text = str(self.item).strip()
        if self.success and not item_text:
            item_text = '빵원 기록'
        day, item, amount = validate(str(self.day), item_text, int(raw))
        view = SaveView(self.user, self.guild, day, item, amount, self.original)
        view.success = self.success
        view.remove_item(view.choose)
        content = f'{day} · {discord.utils.escape_markdown(item)} · {amount:,}원\n'
        if self.success:
            content += '빵원 성공!\n'
        await interaction.response.send_message(content + '저장을 눌러 완료하세요.', view=view, ephemeral=False, allowed_mentions=discord.AllowedMentions.none())

    async def on_error(self, interaction, error):
        await reply_error(interaction, error)


class FirstChoiceView(PrivateView):
    def __init__(self, user, guild, original=None):
        super().__init__(user)
        self.guild, self.original = guild, original
        self.opened = False

    @discord.ui.select(placeholder='먼저 빵원 성공 여부를 선택하세요', options=[
        discord.SelectOption(label='빵원 성공', value='1', description='항목·금액을 비워도 됩니다'),
        discord.SelectOption(label='빵원 미성공', value='0', description='항목·금액을 입력합니다')])
    async def choose(self, interaction, select):
        async with self.lock:
            if self.opened:
                raise ValueError('이미 입력 창을 열었습니다. 취소했다면 명령을 다시 실행해 주세요.')
            await interaction.response.send_modal(EntryModal(self.user, self.guild, self.original, select.values[0] == '1'))
            self.opened = True
            self.stop()
            try:
                await interaction.edit_original_response(content='입력 창을 열었습니다. 취소했다면 명령을 다시 실행해 주세요.', view=None)
            except discord.HTTPException:
                logging.warning('Could not remove initial selection menu')


class DeleteView(PrivateView):
    def __init__(self, user, guild, row):
        super().__init__(user)
        self.guild, self.row, self.done = guild, row, False

    @discord.ui.button(label='삭제 확정', style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        async with self.lock:
            if self.done:
                raise ValueError('이미 처리되었습니다.')
            await interaction.response.defer()
            await asyncio.to_thread(ledger.delete, self.guild, self.user, self.row['id'], self.row['version'])
            self.done = True
            self.stop()
            content = await daily_receipt(self.guild, self.user, self.row['day'])
            await interaction.edit_original_response(content='삭제했습니다.\n' + content, view=None)

    @discord.ui.button(label='취소')
    async def cancel(self, interaction, button):
        async with self.lock:
            self.done = True
            self.stop()
            await interaction.response.edit_message(content='삭제를 취소했습니다.', view=None)


class Client(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.none(), allowed_mentions=discord.AllowedMentions.none())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        guild_id = os.getenv('DISCORD_GUILD_ID', '').strip()
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()


client = Client()
group = app_commands.Group(name='가계부', description='개인별 가계부 입력과 결산', guild_only=True)


@group.command(name='입력', description='빵원 성공 여부 선택 후 날짜·항목·금액 입력')
async def add(interaction: discord.Interaction):
    await interaction.response.send_message('빵원 성공 여부를 먼저 선택해 주세요.', view=FirstChoiceView(interaction.user.id, interaction.guild_id), ephemeral=False)


@group.command(name='수정', description='본인 기록을 ID로 수정')
async def edit(interaction: discord.Interaction, 기록번호: int):
    row = ledger.get(interaction.guild_id, interaction.user.id, 기록번호)
    if row is None:
        raise ValueError('본인의 기록을 찾을 수 없습니다.')
    await interaction.response.send_message('수정할 빵원 성공 여부를 먼저 선택해 주세요.', view=FirstChoiceView(interaction.user.id, interaction.guild_id, row), ephemeral=False)


@group.command(name='삭제', description='본인 기록 삭제 (확인 버튼 표시)')
async def delete(interaction: discord.Interaction, 기록번호: int):
    row = ledger.get(interaction.guild_id, interaction.user.id, 기록번호)
    if row is None:
        raise ValueError('본인의 기록을 찾을 수 없습니다.')
    await interaction.response.send_message(f'ID {row["id"]} · {row["day"]} · {row["amount"]:,}원 기록을 삭제할까요?', ephemeral=False,
        view=DeleteView(interaction.user.id, interaction.guild_id, row))


@group.command(name='목록', description='해당 월의 기록 조회 (한 페이지 10개)')
async def listing(interaction: discord.Interaction, 기준일: str = '', 페이지: app_commands.Range[int, 1, 1000000] = 1):
    day = parse_date(기준일) if 기준일 else datetime.now(ZONE).date()
    start, end = month_bounds(day)
    await interaction.response.defer(ephemeral=False)
    rows, _ = await asyncio.to_thread(ledger.rows, interaction.guild_id, interaction.user.id, start, end, 페이지)
    # Headings render larger in normal message content, rather than embed text.
    groups = {}
    for row in rows:
        groups.setdefault(row['day'], []).append(row)
    chunks = []
    chunk = ''
    for day_text, entries in groups.items():
        day_date = parse_date(day_text)
        heading = f'## {day_date.year}년 {day_date.month}월 {day_date.day}일\n'
        block = heading
        for r in entries:
            line = f'{discord.utils.escape_markdown(r["item"])} · {r["amount"]:,}원\n'
            if len(block) + len(line) > 1800:
                if chunk:
                    chunks.append(chunk)
                    chunk = ''
                chunks.append(block.rstrip())
                block = heading
            block += line
        _, success_days = await asyncio.to_thread(ledger.summary, interaction.guild_id, interaction.user.id, day_text, day_text)
        if success_days:
            block += '빵원 성공!'
        block = block.rstrip()
        if len(chunk) + len(block) + 2 > 1900:
            chunks.append(chunk)
            chunk = ''
        chunk += ('\n\n' if chunk else '') + block
    if chunk:
        chunks.append(chunk)
    for content in chunks or ['표시할 기록이 없습니다.']:
        await interaction.followup.send(content, ephemeral=False, allowed_mentions=discord.AllowedMentions.none())


@group.command(name='기록번호', description='수정·삭제에 필요한 본인 기록번호 확인')
async def record_ids(interaction: discord.Interaction, 기준일: str = '', 페이지: app_commands.Range[int, 1, 1000000] = 1):
    day = parse_date(기준일) if 기준일 else datetime.now(ZONE).date()
    start, end = month_bounds(day)
    await interaction.response.defer(ephemeral=False)
    rows, _ = await asyncio.to_thread(ledger.rows, interaction.guild_id, interaction.user.id, start, end, 페이지)
    lines = [f'기록번호 {r["id"]} · {r["day"]} · {discord.utils.escape_markdown(r["item"])} · {r["amount"]:,}원' for r in rows]
    await interaction.followup.send(embed=discord.Embed(description='\n'.join(lines) or '표시할 기록이 없습니다.'), ephemeral=False)


async def report(interaction, reference, bounds):
    day = parse_date(reference) if reference else datetime.now(ZONE).date()
    start, end = bounds(day)
    await interaction.response.defer(ephemeral=False)
    total, days = await asyncio.to_thread(ledger.summary, interaction.guild_id, interaction.user.id, start, end)
    await interaction.followup.send(f'{start} ~ {end}\n총지출: {total:,}원\n빵원 성공: {days}일 (날짜 중복 제외)', ephemeral=False)


@group.command(name='주간', description='기준일이 포함된 월요일~일요일 결산')
async def weekly(interaction: discord.Interaction, 기준일: str = ''):
    await report(interaction, 기준일, week_bounds)


@group.command(name='월간', description='기준일이 포함된 달의 결산')
async def monthly(interaction: discord.Interaction, 기준일: str = ''):
    await report(interaction, 기준일, month_bounds)


@client.tree.error
async def command_error(interaction, error):
    await reply_error(interaction, getattr(error, 'original', error))


client.tree.add_command(group)

if __name__ == '__main__':
    token = os.getenv('DISCORD_TOKEN', '').strip()
    if not token or token == 'YOUR_BOT_TOKEN_HERE':
        raise SystemExit('.env 파일에 DISCORD_TOKEN을 설정해 주세요.')
    client.run(token)
