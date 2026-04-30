import com.credits.scapi.v0.SmartContract;
import com.credits.scapi.v0.BasicStandard;

import java.math.BigDecimal;
import java.util.HashMap;
import java.util.Map;

public class EBB extends SmartContract implements BasicStandard {

    private final String owner;
    private final String name = "EBB";
    private final String symbol = "EBB";
    private final int decimals = 18;
    private BigDecimal totalSupplyValue;
    private boolean frozen = false;
    private Map<String, BigDecimal> balances = new HashMap<>();
    private Map<String, Map<String, BigDecimal>> allowances = new HashMap<>();

    public EBB() {
        super();
        owner = initiator;
        totalSupplyValue = new BigDecimal("1000000");
        balances.put(owner, totalSupplyValue);
    }

    public String getName() {
        return name;
    }

    public String getSymbol() {
        return symbol;
    }

    public int getDecimal() {
        return decimals;
    }

    public String totalSupply() {
        return totalSupplyValue.toString();
    }

    public String balanceOf(String address) {
        BigDecimal b = balances.get(address);
        return (b == null ? BigDecimal.ZERO : b).toString();
    }

    public String allowance(String tokenOwner, String spender) {
        Map<String, BigDecimal> ownerAllow = allowances.get(tokenOwner);
        if (ownerAllow == null) return BigDecimal.ZERO.toString();
        BigDecimal a = ownerAllow.get(spender);
        return (a == null ? BigDecimal.ZERO : a).toString();
    }

    public boolean transfer(String to, String amount) {
        if (frozen) throw new RuntimeException("EBB transfers are paused");
        BigDecimal amt = new BigDecimal(amount);
        if (amt.signum() <= 0) throw new RuntimeException("amount must be positive");
        BigDecimal fromBal = balances.get(initiator);
        if (fromBal == null) fromBal = BigDecimal.ZERO;
        if (fromBal.compareTo(amt) < 0) throw new RuntimeException("insufficient EBB balance");
        balances.put(initiator, fromBal.subtract(amt));
        BigDecimal toBal = balances.get(to);
        if (toBal == null) toBal = BigDecimal.ZERO;
        balances.put(to, toBal.add(amt));
        return true;
    }

    public boolean transferFrom(String from, String to, String amount) {
        if (frozen) throw new RuntimeException("EBB transfers are paused");
        BigDecimal amt = new BigDecimal(amount);
        if (amt.signum() <= 0) throw new RuntimeException("amount must be positive");
        Map<String, BigDecimal> fromAllow = allowances.get(from);
        if (fromAllow == null) throw new RuntimeException("no allowance set");
        BigDecimal allowed = fromAllow.get(initiator);
        if (allowed == null) allowed = BigDecimal.ZERO;
        if (allowed.compareTo(amt) < 0) throw new RuntimeException("allowance exceeded");
        BigDecimal fromBal = balances.get(from);
        if (fromBal == null) fromBal = BigDecimal.ZERO;
        if (fromBal.compareTo(amt) < 0) throw new RuntimeException("insufficient EBB balance");
        balances.put(from, fromBal.subtract(amt));
        BigDecimal toBal = balances.get(to);
        if (toBal == null) toBal = BigDecimal.ZERO;
        balances.put(to, toBal.add(amt));
        fromAllow.put(initiator, allowed.subtract(amt));
        return true;
    }

    public void approve(String spender, String amount) {
        BigDecimal amt = new BigDecimal(amount);
        if (amt.signum() < 0) throw new RuntimeException("amount must be non-negative");
        Map<String, BigDecimal> ownerAllow = allowances.get(initiator);
        if (ownerAllow == null) {
            ownerAllow = new HashMap<>();
            allowances.put(initiator, ownerAllow);
        }
        ownerAllow.put(spender, amt);
    }

    public boolean burn(String amount) {
        BigDecimal amt = new BigDecimal(amount);
        if (amt.signum() <= 0) throw new RuntimeException("amount must be positive");
        BigDecimal bal = balances.get(initiator);
        if (bal == null) bal = BigDecimal.ZERO;
        if (bal.compareTo(amt) < 0) throw new RuntimeException("insufficient EBB to burn");
        balances.put(initiator, bal.subtract(amt));
        totalSupplyValue = totalSupplyValue.subtract(amt);
        return true;
    }

    public boolean setFrozen(boolean newFrozen) {
        if (!initiator.equals(owner)) throw new RuntimeException("only owner can pause");
        frozen = newFrozen;
        return frozen;
    }
}
