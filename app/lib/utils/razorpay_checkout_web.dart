// Razorpay Standard Checkout bridge for Flutter web.
// ignore: avoid_web_libraries_in_flutter
import 'dart:async';
import 'dart:html' as html;
import 'dart:js' as js;
import 'dart:js_util' as js_util;

/// Opens the Razorpay payment modal. Returns payment ids on success, null if dismissed.
Future<Map<String, String>?> openRazorpayStandardCheckout({
  required String keyId,
  required String orderId,
  required int amountPaise,
  required String currency,
  required String description,
  required String customerName,
  required String customerEmail,
  bool testMode = false,
}) async {
  if (js.context['Razorpay'] == null) {
    throw StateError(
      'Razorpay checkout.js is not loaded. Add the script to web/index.html.',
    );
  }

  final completer = Completer<Map<String, String>?>();

  js.context['__rzpSuccess'] = js.allowInterop(
    (String orderId, String paymentId, String signature) {
      if (!completer.isCompleted) {
        completer.complete({
          'razorpay_order_id': orderId,
          'razorpay_payment_id': paymentId,
          'razorpay_signature': signature,
        });
      }
    },
  );
  js.context['__rzpDismiss'] = js.allowInterop(() {
    if (!completer.isCompleted) completer.complete(null);
  });
  js.context['__rzpFailed'] = js.allowInterop((String message) {
    if (!completer.isCompleted) {
      completer.completeError(Exception(message.isEmpty ? 'Payment failed' : message));
    }
  });

  final opts = js_util.jsify({
    'key': keyId,
    'order_id': orderId,
    'amount': amountPaise,
    'currency': currency,
    'name': 'HirePanda',
    'description': description,
    'prefill': {
      'name': customerName,
      'email': customerEmail,
    },
    'theme': {'color': '#1E3A8A'},
    'test_mode': testMode,
  });

  try {
    js_util.callMethod(html.window, 'openRazorpayStandardCheckout', [opts]);
  } catch (e) {
    if (!completer.isCompleted) {
      completer.completeError(e);
    }
  }

  return completer.future;
}
