# zammad-overrides/teltonika.rb
#
# Nativer SMS-Connector fuer ein Teltonika-RUT-Router-Gateway (cgi-bin
# sms_send). Wird per Bind-Mount nach
# /opt/zammad/app/models/channel/driver/sms/teltonika.rb eingehaengt --
# Zammad entdeckt SMS-Connectors per Dateisystem-Scan
# (app/controllers/channels_sms_controller.rb#channels_config), keine
# weitere Registrierung noetig.
#
# MINIMAL-SPIKE: kein Mehrteil-Splitting, kein Sende-Budget/Rate-Limit,
# kein Ueberlauf-Handling -- bewusst kleiner als der bestehende
# SMSammad-Python-Pfad (ticket_to_sms.py), nur um die native
# SMS-Sprechblase im Ticket-Editor zu testen. Laengere Texte werden vom
# Router NICHT automatisch aufgeteilt (siehe smsammad-README, Abschnitt
# "Teltonika-API-Dokumentation und ihre Luecken") -- koennen also
# abgeschnitten/verschluckt werden.
#
# Nach jedem Zammad-Update pruefen mit:
#   docker compose exec zammad-railsserver /docker-entrypoint.sh \
#     bundle exec rails r 'p Channel::Driver::Sms::Teltonika.definition'

class Channel::Driver::Sms::Teltonika < Channel::Driver::Sms::Base
  NAME = 'sms/teltonika'.freeze

  def fetchable?(_channel)
    false
  end

  def deliver(options, attr, _notification = false)
    return true if Setting.get('import_mode')

    send_sms(options, attr[:recipient], attr[:message])
    true
  end

  def self.definition
    fields = [
      { name: 'options::host', display: __('Router-Host'), tag: 'input', type: 'text', limit: 200, null: false, placeholder: '192.168.1.1' },
      { name: 'options::username', display: __('Benutzername'), tag: 'input', type: 'text', limit: 200, null: false },
      { name: 'options::password', display: __('Passwort'), tag: 'input', type: 'text', limit: 200, null: false },
    ]
    {
      name:         'Teltonika RUT240',
      adapter:      'sms/teltonika',
      account:      fields + [
        { name: 'group_id', display: __('Destination Group'), tag: 'tree_select', null: false, relation: 'Group', nulloption: true, filter: { active: true } },
      ],
      notification: fields,
    }
  end

  private

  # Bewusst wie im Python-Client (teltonika.py): kein Exception-Chaining,
  # damit die Original-Exception (koennte die URL inkl. Passwort als
  # Query-Parameter enthalten) nirgends im Log landet.
  def send_sms(options, recipient, message)
    uri = URI("https://#{options[:host]}/cgi-bin/sms_send")
    uri.query = URI.encode_www_form(
      username: options[:username],
      password: options[:password],
      number:   recipient,
      text:     message,
    )
    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = true
    http.verify_mode = OpenSSL::SSL::VERIFY_NONE # selbstsigniertes Zertifikat
    response = http.get(uri)
    return if response.is_a?(Net::HTTPSuccess)

    raise "Teltonika sms_send: HTTP #{response.code}"
  rescue => e
    raise "Teltonika sms_send fehlgeschlagen: #{e.class}"
  end
end
