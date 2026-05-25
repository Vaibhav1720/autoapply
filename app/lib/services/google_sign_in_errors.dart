/// User-visible Google sign-in failure (readable in release/minified web builds).
class GoogleSignInException implements Exception {
  GoogleSignInException(this.message);
  final String message;

  @override
  String toString() => message;
}

String formatSignInError(Object error) {
  if (error is GoogleSignInException) return error.message;
  if (error is StateError) {
    final msg = error.message;
    if (msg.isNotEmpty) return msg;
  }
  final text = error.toString();
  if (text.startsWith('Instance of ')) {
    return 'Google sign-in failed. Please try again.';
  }
  return text;
}
