from django.core.management.base import BaseCommand, CommandError

from notifications.services.push_sender import PushResult, PushStatus, send_deadline_push
from notifications.services.reminder_service import (
    active_devices_for,
    claim_delivery,
    complete_delivery,
    delivery_is_current,
    due_schedule_events,
    pending_delivery_count,
)


class Command(BaseCommand):
    help = 'Schedule의 D-7/D-1 마감 알림을 활성 기기에 발송한다.'

    def add_arguments(self, parser):
        """단발 Cron 실행에 필요한 안전한 확인/제한 옵션을 제공한다."""
        parser.add_argument('--dry-run', action='store_true', help='실제 발송·DB 변경 없이 확인')
        parser.add_argument('--limit', type=int, help='최대 일정 알림 이벤트 수 (양의 정수)')

    def handle(self, *args, **options):
        """이벤트·기기별 점유 뒤에만 FCM을 호출하고, 원문 식별자는 출력하지 않는다."""
        if options['limit'] is not None and options['limit'] <= 0:
            raise CommandError('--limit는 양의 정수여야 합니다.')
        events = due_schedule_events()
        if options['limit'] is not None:
            events = events[:options['limit']]
        stats = {
            'events': len(events), 'candidates': 0, 'claimed': 0, 'accepted': 0,
            'invalid_devices': 0, 'configuration_errors': 0, 'other_failures': 0,
        }

        if options['dry_run']:
            stats['candidates'] = sum(pending_delivery_count(event) for event in events)
            self.stdout.write(
                'dry-run deadline reminders: '
                f'events={stats["events"]} candidate_deliveries={stats["candidates"]}',
            )
            return

        configuration_failed = False
        for event in events:
            # 기기 목록은 잠금 밖에서 읽고, 개별 기기는 claim_delivery에서 다시 검증한다.
            for device in active_devices_for(event.schedule):
                # 같은 사용자의 재등록으로 스냅샷이 바뀌면 최신 조건으로 한 번 재점유한다.
                for _ in range(2):
                    claim = claim_delivery(
                        schedule_id=event.schedule.pk,
                        days_before=event.days_before,
                        device_id=device.pk,
                    )
                    if claim is None or delivery_is_current(claim):
                        break
                    claim = None
                else:
                    # 계속 갱신되는 기기는 무한 재시도하지 않고 Cron에 실패를 알린다.
                    stats['other_failures'] += 1
                if claim is None:
                    continue
                stats['candidates'] += 1
                stats['claimed'] += 1
                try:
                    result = send_deadline_push(
                        registration_id=claim.device.registration_id,
                        schedule_id=claim.schedule_id,
                        document_id=claim.document_id,
                        deadline_date=claim.deadline_date,
                        days_before=claim.days_before,
                    )
                except Exception:
                    # Adapter가 아닌 호출 경로의 예외도 비밀값 없이 결과 불명으로 남긴다.
                    result = PushResult(PushStatus.UNKNOWN, 'UNEXPECTED_COMMAND_ERROR')
                complete_delivery(claim, result)
                if result.status == PushStatus.ACCEPTED:
                    stats['accepted'] += 1
                elif result.status == PushStatus.INVALID_DEVICE:
                    stats['invalid_devices'] += 1
                elif result.status == PushStatus.CONFIGURATION_ERROR:
                    stats['configuration_errors'] += 1
                    configuration_failed = True
                    break
                else:
                    stats['other_failures'] += 1
            if configuration_failed:
                break

        self.stdout.write(
            'deadline reminders: '
            f'events={stats["events"]} claimed={stats["claimed"]} accepted={stats["accepted"]} '
            f'invalid_devices={stats["invalid_devices"]} '
            f'configuration_errors={stats["configuration_errors"]} '
            f'other_failures={stats["other_failures"]}',
        )
        if configuration_failed:
            raise CommandError('FIREBASE_CONFIGURATION_ERROR: 설정 오류로 나머지 발송을 중단했습니다.')
        if stats['other_failures']:
            raise CommandError('DEADLINE_REMINDER_FAILURE: 일부 발송이 실패했거나 결과를 확인할 수 없습니다.')
