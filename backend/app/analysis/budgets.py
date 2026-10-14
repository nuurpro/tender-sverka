"""Worst-case reservations. Ambiguous usage never releases spent upper bounds."""
from decimal import Decimal
from .schemas import RunBudget


class BudgetExceeded(ValueError):
    pass


class BudgetLedger:
    def __init__(self,budget,profile=None):
        self.budget=budget;self.profile=profile;self.requests=0;self.tokens_spent=0
        self.cost_spent=Decimal(0);self.usage_unknown=False;self.reservations={}

    def reserve(self,input_tokens,output_tokens):
        total=input_tokens+output_tokens
        cost=None
        if self.profile and self.profile.input_price_per_million is not None and self.profile.output_price_per_million is not None:
            cost=(Decimal(input_tokens)*Decimal(self.profile.input_price_per_million)+Decimal(output_tokens)*Decimal(self.profile.output_price_per_million))/1000000
        if self.requests>=self.budget.max_requests or self.tokens_spent+total>self.budget.max_tokens or (cost is not None and self.budget.cost_ceiling is not None and self.cost_spent+cost>Decimal(self.budget.cost_ceiling)):
            raise BudgetExceeded('Run budget exhausted')
        self.requests+=1;self.tokens_spent+=total
        if cost is not None:self.cost_spent+=cost
        identity=self.requests
        self.reservations[identity]=(input_tokens,output_tokens,cost)
        return identity

    def reconcile(self,reservation,usage):
        input_upper,output_upper,cost=self.reservations.pop(reservation)
        if not usage or set(usage)!={'input_tokens','output_tokens'} or any(type(v) is not int or v<0 for v in usage.values()) or usage['input_tokens']>input_upper or usage['output_tokens']>output_upper:
            self.usage_unknown=True
            return
        self.tokens_spent-=input_upper+output_upper-sum(usage.values())
        if cost is not None:
            actual=(Decimal(usage['input_tokens'])*Decimal(self.profile.input_price_per_million)+Decimal(usage['output_tokens'])*Decimal(self.profile.output_price_per_million))/1000000
            self.cost_spent-=cost-actual
