from aiogram.fsm.state import State, StatesGroup


class CreateTicket(StatesGroup):
    waiting_subject = State()
    waiting_message = State()


class ReplyToTicket(StatesGroup):
    waiting_reply = State()


class AdminReplyToTicket(StatesGroup):
    waiting_reply = State()
