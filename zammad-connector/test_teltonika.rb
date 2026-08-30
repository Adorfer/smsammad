# Standalone-Test fuer teltonika.rb -- laeuft OHNE echtes Zammad (nur
# minimale Stubs fuer die Zammad-Basisklasse/-Helfer), verifiziert aber
# echt die HTTP-Bau-Logik gegen einen echten lokalen Test-Server.
#
# Aufruf: ruby zammad-connector/test_teltonika.rb

require 'net/http'
require 'openssl'
require 'socket'
require 'uri'

# -- Minimale Zammad-Stubs, nur was teltonika.rb tatsaechlich braucht --
module Channel
  module Driver
    module Sms
      class Base
        def fetchable?(_channel)
          false
        end
      end
    end
  end
end

module Setting
  def self.get(_name)
    false
  end
end

def __(text)
  text
end

load File.join(__dir__, 'teltonika.rb')

failures = []

def check(failures, name)
  yield
  puts "OK   #{name}"
rescue => e
  failures << "#{name}: #{e.class}: #{e.message}"
  puts "FAIL #{name}: #{e.class}: #{e.message}"
end

check(failures, 'definition() liefert erwartete Struktur') do
  definition = Channel::Driver::Sms::Teltonika.definition
  raise 'adapter falsch' if definition[:adapter] != 'sms/teltonika'
  raise 'account-Felder fehlen' if definition[:account].none? { |f| f[:name] == 'options::host' }
  raise 'notification-Felder fehlen' if definition[:notification].none? { |f| f[:name] == 'options::password' }
  raise 'group_id fehlt im account' if definition[:account].none? { |f| f[:name] == 'group_id' }
end

# -- Echter lokaler HTTPS-Test-Server (selbstsigniertes Zertifikat, wie
# beim echten Teltonika-Router) --
def start_test_server
  cert = OpenSSL::X509::Certificate.new
  key = OpenSSL::PKey::RSA.new(2048)
  cert.version = 2
  cert.serial = 1
  cert.subject = OpenSSL::X509::Name.parse('/CN=localhost')
  cert.issuer = cert.subject
  cert.public_key = key.public_key
  cert.not_before = Time.now
  cert.not_after = Time.now + 60
  cert.sign(key, OpenSSL::Digest.new('SHA256'))

  tcp_server = TCPServer.new('127.0.0.1', 0)
  port = tcp_server.addr[1]
  ssl_context = OpenSSL::SSL::SSLContext.new
  ssl_context.cert = cert
  ssl_context.key = key
  ssl_server = OpenSSL::SSL::SSLServer.new(tcp_server, ssl_context)

  received = { request_line: nil }
  thread = Thread.new do
    socket = ssl_server.accept
    received[:request_line] = socket.gets
    socket.print "HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK"
    socket.close
  rescue StandardError
    nil
  end

  [port, received, thread, ssl_server]
end

check(failures, 'deliver() schickt korrekte Query-Parameter an sms_send') do
  port, received, thread, ssl_server = start_test_server
  begin
    driver = Channel::Driver::Sms::Teltonika.new
    options = { host: "127.0.0.1:#{port}", username: 'testuser', password: 'geheim+/&' }
    result = driver.deliver(options, { recipient: '004915112345678', message: 'Hallo Welt' })
    thread.join(2)

    raise 'deliver() lieferte nicht true' if result != true
    raise 'kein Request empfangen' if received[:request_line].nil?

    line = received[:request_line]
    raise "falscher Pfad: #{line}" if !line.include?('/cgi-bin/sms_send')
    raise 'username fehlt in Query' if !line.include?('username=testuser')
    raise 'number fehlt in Query' if !line.include?('number=004915112345678')
    raise 'text fehlt in Query' if !line.include?('text=Hallo')
    # Passwort mit Sonderzeichen muss URL-encoded sein (kein rohes '+'/'&')
    raise 'Passwort nicht korrekt encodiert' if !line.include?('password=geheim%2B%2F%26')
  ensure
    ssl_server.close
  end
end

check(failures, 'deliver() wirft bei HTTP-Fehler eine Exception ohne URL/Passwort') do
  port, _received, thread, ssl_server = start_test_server
  # Server mit dieser Verbindung schliessen wir sofort wieder, um einen
  # Fehler zu provozieren (Connection Refused durch geschlossenen Port).
  ssl_server.close
  thread.join(1)

  driver = Channel::Driver::Sms::Teltonika.new
  options = { host: "127.0.0.1:#{port}", username: 'u', password: 'geheimes-passwort' }
  begin
    driver.deliver(options, { recipient: '123', message: 'x' })
    raise 'haette eine Exception werfen muessen'
  rescue => e
    raise "Passwort im Fehlertext gelandet: #{e.message}" if e.message.include?('geheimes-passwort')
    raise "Host im Fehlertext gelandet: #{e.message}" if e.message.include?(options[:host])
  end
end

puts
if failures.empty?
  puts 'Alle Tests OK.'
else
  puts "#{failures.size} Test(s) fehlgeschlagen:"
  failures.each { |f| puts "  - #{f}" }
  exit 1
end
