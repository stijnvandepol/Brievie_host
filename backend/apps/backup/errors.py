from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _

# Dutch (B1) reasons shown to the user; the code is what is stored and tested.
REASONS: dict[str, Promise] = {
    "not_configured": _("Er is nog geen back-up ingesteld."),
    "destination_conflict": _(
        "Je kunt maar één bestemming instellen: een map of een adres. Haal er één weg."
    ),
    "destination_not_writable": _(
        "De back-updienst kan niet schrijven op deze bestemming. "
        "Kijk in SELFHOST.md hoe je de schijf klaarzet."
    ),
    "destination_not_ready": _("De bestemming voor de back-up is nog niet klaar voor gebruik."),
    "same_disk": _("De back-up staat op dezelfde schijf als de gegevens. Kies een andere schijf."),
    "already_initialised": _("Op deze bestemming staat al een back-up."),
    "repository_has_credentials": _(
        "Het adres van de back-up bevat een gebruikersnaam of wachtwoord. Haal die eruit."
    ),
    "repository_unsupported": _("Dit soort bestemming wordt niet ondersteund."),
    "repository_missing": _("Op de bestemming is geen back-up gevonden."),
    "wrong_password": _("Het wachtwoord van de back-up klopt niet."),
    "repository_locked": _("De back-up is vergrendeld door een ander proces."),
    "destination_unreachable": _("De bestemming van de back-up is niet bereikbaar."),
    "destination_full": _("Er is geen ruimte meer op de bestemming van de back-up."),
    "files_incomplete": _(
        "De back-up is gemaakt, maar is niet compleet: "
        "een of meer bestanden konden niet worden gelezen."
    ),
    "dump_failed": _("De database kon niet worden veiliggesteld."),
    "backup_role_missing": _(
        "De back-upgebruiker van de database ontbreekt of heeft te weinig rechten."
    ),
    "pg_client_too_old": _("De PostgreSQL-programma's zijn ouder dan de database."),
    "no_backup_password": _("Er is geen wachtwoord voor de back-up ingesteld."),
    "busy": _("Er is al een back-up of herstel bezig."),
    "env_copy_has_secret": _("De kopie van de instellingen bevat een geheim."),
    "manifest_unreadable": _("De inhoudsopgave van de back-up kon niet worden gelezen."),
    "newer_backup": _("Deze back-up komt van een nieuwere versie van Brievie."),
    "run_not_found": _("Deze back-up is niet gevonden."),
    "wrong_master_key": _("De hoofdsleutel hoort niet bij deze back-up."),
    "restore_failed": _("Het herstel is niet gelukt."),
    "backup_folder_unwritable": _(
        "De naam van de back-upmap bevat een aanhalingsteken of een regeleinde. "
        "Kies een map met een gewone naam."
    ),
    "master_key_malformed": _(
        "De hoofdsleutel moet 64 tekens hebben: cijfers en de letters a tot en met f."
    ),
    "backup_password_unwritable": _(
        "Het aparte wachtwoord van de back-up bevat een aanhalingsteken of een regeleinde. "
        "Zo'n wachtwoord past niet in de instellingen."
    ),
    "interrupted_by_restore": _("Afgebroken door herstel."),
    "abandoned": _("De vorige back-up is afgebroken. Er is een nieuwe gestart."),
    "internal_error": _("Er ging iets mis. Probeer het later opnieuw."),
    "restic_failed": _("Het back-upprogramma gaf een fout."),
}


class BackupError(Exception):
    """A back-up or restore step failed for a known reason.

    Carries only the code: never a path, password or command output.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code

    @property
    def message(self) -> str:
        return str(REASONS[self.code])
