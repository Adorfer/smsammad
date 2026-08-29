"""CLI-Einstieg fuer zammad2teltonikasms.

Zwei Richtungen sind vorgesehen (Logik jeweils noch nicht implementiert):
- ticket-to-sms: Zammad-Ereignis -> SMS ueber Teltonika-Router
- sms-to-ticket: eingehende SMS am Teltonika-Router -> Zammad-Ticket
"""

import argparse


def ticket_to_sms(args: argparse.Namespace) -> None:
    raise NotImplementedError

def sms_to_ticket(args: argparse.Namespace) -> None:
    raise NotImplementedError


def main() -> None:
    parser = argparse.ArgumentParser(prog="zammad2teltonikasms")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("ticket-to-sms").set_defaults(func=ticket_to_sms)
    subparsers.add_parser("sms-to-ticket").set_defaults(func=sms_to_ticket)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
